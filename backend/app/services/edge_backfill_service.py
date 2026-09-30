"""Orchestrate edge storage failover backfill (RDSO 18.3.16)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId

from app.core.database import camera_collection
from app.services.camera_identity import get_camera_by_ref
from app.services.edge_backfill_jobs import (
    create_job,
    ensure_edge_backfill_indexes,
    find_active_job_by_key,
    find_completed_job_by_key,
    get_job,
    list_jobs,
    update_job,
)
from app.services.edge_capability import detect_edge_storage_capability
from app.services.edge_gap_detection import detect_recording_gaps, merge_edge_clips_into_gaps
from app.services.edge_ingest import (
    download_edge_clip_to_file,
    ingest_edge_media_as_session,
    search_edge_clips,
)
from app.services.edge_storage_types import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PARTIAL,
    JOB_PENDING,
    JOB_RUNNING,
    job_key,
    normalize_interval,
    parse_iso,
    to_iso,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

_runner_tasks: dict[str, asyncio.Task] = {}


async def _load_camera(camera_ref: str) -> dict:
    cam = await get_camera_by_ref(camera_ref)
    if not cam:
        # Fallback direct id
        try:
            cam = await camera_collection.find_one({"_id": ObjectId(camera_ref)})
        except Exception:
            cam = None
    if not cam:
        raise ValueError(f"Camera not found: {camera_ref}")
    cam = dict(cam)
    cam["id"] = str(cam.get("_id"))
    return cam


async def get_edge_capability_for_camera(camera_ref: str) -> dict[str, Any]:
    camera = await _load_camera(camera_ref)
    cap = await detect_edge_storage_capability(camera)
    cap["camera_id"] = str(camera.get("_id"))
    cap["camera_name"] = camera.get("name") or ""
    return cap


async def preview_edge_backfill(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
) -> dict[str, Any]:
    """Detect gaps and query edge for overlapping footage (no download)."""
    camera = await _load_camera(camera_ref)
    gaps_info = await detect_recording_gaps(str(camera["_id"]), range_start, range_end)
    cap = await detect_edge_storage_capability(camera)
    edge_clips: list[dict] = []
    if cap.get("supported"):
        edge_clips = await search_edge_clips(
            camera,
            range_start,
            range_end,
            protocol=cap.get("protocol"),
        )
    annotated = merge_edge_clips_into_gaps(gaps_info.get("gaps") or [], edge_clips)
    return {
        **gaps_info,
        "capability": cap,
        "edge_clips_total": len(edge_clips),
        "gaps": annotated,
        "recoverable_gap_count": sum(1 for g in annotated if g.get("edge_clip_count")),
    }


async def start_edge_backfill(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
    *,
    confirm: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Create idempotent backfill job(s) for gaps in the range and run them."""
    if not confirm:
        raise ValueError("confirm=true is required to start edge backfill")

    await ensure_edge_backfill_indexes()
    camera = await _load_camera(camera_ref)
    camera_id = str(camera["_id"])
    range_start, range_end = normalize_interval(range_start, range_end)

    cap = await detect_edge_storage_capability(camera)
    if not cap.get("supported"):
        return {
            "ok": False,
            "edge_supported": False,
            "message": cap.get("message")
            or "Camera has no edge storage retrieval support; nothing fabricated",
            "capability": cap,
            "jobs": [],
        }

    gaps_info = await detect_recording_gaps(camera_id, range_start, range_end)
    gaps = gaps_info.get("gaps") or []
    if not gaps:
        return {
            "ok": True,
            "edge_supported": True,
            "message": "No VMS recording gaps in the requested range",
            "capability": cap,
            "jobs": [],
            "gaps": [],
        }

    jobs_out: list[dict] = []
    for g in gaps:
        gs = parse_iso(g["start"])
        ge = parse_iso(g["end"])
        if gs is None or ge is None:
            continue
        key = job_key(camera_id, gs, ge)

        active = await find_active_job_by_key(key)
        if active and not force:
            jobs_out.append(
                {
                    **(await get_job(str(active["_id"])) or {}),
                    "idempotent_reuse": True,
                    "message": "Existing pending/running job for this camera/time range",
                }
            )
            continue

        completed = await find_completed_job_by_key(key)
        if completed and not force:
            pub = await get_job(str(completed["_id"]))
            if pub:
                pub = {**pub, "idempotent_reuse": True}
                jobs_out.append(pub)
            continue

        job = await create_job(
            {
                "camera_id": camera_id,
                "camera_uid": camera.get("camera_uid") or "",
                "job_key": key,
                "status": JOB_PENDING,
                "gap_start": to_iso(gs),
                "gap_end": to_iso(ge),
                "protocol": cap.get("protocol"),
                "edge_supported": True,
                "message": "Queued for edge backfill",
            }
        )
        jobs_out.append(job)
        _schedule_job(job["id"], camera)

    return {
        "ok": True,
        "edge_supported": True,
        "message": f"Started/reused {len(jobs_out)} backfill job(s)",
        "capability": cap,
        "gaps": gaps,
        "jobs": jobs_out,
    }


def _schedule_job(job_id: str, camera: dict) -> None:
    existing = _runner_tasks.get(job_id)
    if existing and not existing.done():
        return

    async def _run():
        try:
            await execute_backfill_job(job_id, camera)
        except Exception as exc:
            logger.exception("[EDGE] job %s crashed: %s", job_id, exc)
            await update_job(
                job_id,
                {
                    "status": JOB_FAILED,
                    "error": str(exc),
                    "finished_at": utc_now_iso(),
                },
            )
        finally:
            _runner_tasks.pop(job_id, None)

    _runner_tasks[job_id] = asyncio.create_task(_run())


async def execute_backfill_job(job_id: str, camera: Optional[dict] = None) -> dict[str, Any]:
    job = await get_job(job_id)
    if not job:
        raise ValueError("job not found")
    if camera is None:
        camera = await _load_camera(job["camera_id"])

    await update_job(
        job_id,
        {
            "status": JOB_RUNNING,
            "started_at": utc_now_iso(),
            "message": "Searching edge storage for gap footage",
        },
    )

    gs = parse_iso(job["gap_start"])
    ge = parse_iso(job["gap_end"])
    if gs is None or ge is None:
        await update_job(
            job_id,
            {"status": JOB_FAILED, "error": "invalid gap bounds", "finished_at": utc_now_iso()},
        )
        return (await get_job(job_id)) or job

    protocol = job.get("protocol") or ""
    clips = await search_edge_clips(camera, gs, ge, protocol=protocol)
    await update_job(
        job_id,
        {
            "edge_clips_found": len(clips),
            "message": f"Found {len(clips)} edge clip(s)",
        },
    )

    if not clips:
        await update_job(
            job_id,
            {
                "status": JOB_FAILED,
                "error": None,
                "message": "No edge footage available for this gap (not fabricated)",
                "finished_at": utc_now_iso(),
            },
        )
        # Use failed vs completed empty — requirement: report clearly when no footage
        return (await get_job(job_id)) or job

    session_ids: list[str] = []
    bytes_total = 0
    errors: list[str] = []
    ingested = 0

    with tempfile.TemporaryDirectory(prefix="edge_backfill_") as tmp:
        tmp_path = Path(tmp)
        for idx, clip in enumerate(clips):
            cs = parse_iso(clip.get("overlap_start") or clip.get("start") or clip.get("startTime"))
            ce = parse_iso(clip.get("overlap_end") or clip.get("end") or clip.get("endTime"))
            if cs is None or ce is None:
                continue
            # Clamp to gap
            cs = max(cs, gs)
            ce = min(ce, ge)
            if ce <= cs:
                continue
            dest = tmp_path / f"clip_{idx:03d}.bin"
            try:
                nbytes = await download_edge_clip_to_file(
                    camera, clip, dest, protocol=str(protocol)
                )
                bytes_total += int(nbytes or 0)
                if not dest.is_file() or dest.stat().st_size <= 0:
                    # download may have written .mkv
                    alt = dest.with_suffix(".mkv")
                    if alt.is_file():
                        dest = alt
                    else:
                        raise RuntimeError("download produced empty file")
                session = await ingest_edge_media_as_session(
                    camera,
                    media_path=dest,
                    clip_start=cs,
                    clip_end=ce,
                    job_id=job_id,
                    protocol=str(protocol),
                )
                sid = session.get("id")
                if sid:
                    session_ids.append(sid)
                    ingested += 1
            except Exception as exc:
                logger.warning("[EDGE] clip ingest failed job=%s: %s", job_id, exc)
                errors.append(str(exc))

    if ingested == 0:
        status = JOB_FAILED
        message = "Edge clips found but transfer/ingest failed"
    elif errors or ingested < len(clips):
        status = JOB_PARTIAL
        message = f"Ingested {ingested}/{len(clips)} edge clip(s); some missing or failed"
    else:
        # Full clip ingest OK — still partial if edge coverage does not fill the whole gap
        covered_end = gs
        for sid_clip in clips:
            ce = parse_iso(
                sid_clip.get("overlap_end") or sid_clip.get("end") or sid_clip.get("endTime")
            )
            cs = parse_iso(
                sid_clip.get("overlap_start") or sid_clip.get("start") or sid_clip.get("startTime")
            )
            if cs and ce:
                covered_end = max(covered_end, min(ce, ge))
        if covered_end + timedelta(seconds=2) < ge:
            status = JOB_PARTIAL
            message = (
                f"Backfill partial — {ingested} session(s) ingested; "
                "edge footage does not fully cover the VMS gap"
            )
        else:
            status = JOB_COMPLETED
            message = f"Backfill completed — {ingested} session(s) ingested"

    await update_job(
        job_id,
        {
            "status": status,
            "session_ids": session_ids,
            "bytes_downloaded": bytes_total,
            "message": message,
            "error": "; ".join(errors) if errors else None,
            "finished_at": utc_now_iso(),
        },
    )
    return (await get_job(job_id)) or job


async def list_edge_jobs(camera_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    await ensure_edge_backfill_indexes()
    return await list_jobs(camera_id=camera_id, limit=limit)


async def get_edge_job(job_id: str) -> Optional[dict]:
    return await get_job(job_id)
