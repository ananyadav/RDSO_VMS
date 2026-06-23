"""Storage dashboard — filesystem + MongoDB recording statistics."""

from __future__ import annotations

import logging
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import psutil

from app.core.database import camera_collection, recording_status_logs_collection
from app.services.recording_config import (
    RECORDING_STREAM,
    get_recording_stream_info,
    get_retention_policy,
    recording_stream_profile,
)
from app.services.recording_metrics import _prev_snapshot
from app.services.camera_identity import (
    resolve_camera_uid,
    storage_folder_keys_for_uid,
)
from app.services.recording_storage import mapped_legacy_folder_ids
from app.services.video_recording import (
    RECORDINGS_DIR,
    _empty_session_stats,
    is_camera_recording,
)

logger = logging.getLogger(__name__)

_FS_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="storage-fs")


def _disk_usage_for_recordings() -> dict:
    """System disk stats for the volume that holds Recordings/."""
    path = str(RECORDINGS_DIR.resolve())
    try:
        disk = psutil.disk_usage(path)
    except Exception as e:
        logger.warning(f"[STORAGE] disk_usage failed for {path}: {e}")
        disk = psutil.disk_usage("/" if os.name != "nt" else "C:\\")

    total_gb = round(disk.total / 1024**3, 2)
    used_gb = round(disk.used / 1024**3, 2)
    free_gb = round(disk.free / 1024**3, 2)
    free_percent = round(disk.free / disk.total * 100, 1) if disk.total else 0.0
    if free_percent > 20:
        status_level, status_label = "green", "Healthy"
    elif free_percent > 10:
        status_level, status_label = "yellow", "Low"
    else:
        status_level, status_label = "red", "Critical"
    return {
        "disk_path": path,
        "disk_total_gb": total_gb,
        "disk_used_gb": used_gb,
        "disk_free_gb": free_gb,
        "disk_free_percent": free_percent,
        "disk_percent": round(disk.percent, 1),
        "status_level": status_level,
        "status_label": status_label,
    }


def _camera_filesystem_stats(camera_id: str) -> dict:
    """Aggregate segment stats under Recordings/{camera_id}/ (single-pass walk)."""
    cam_dir = RECORDINGS_DIR / camera_id
    if not cam_dir.is_dir():
        return {**_empty_session_stats(), "session_count": 0}

    segment_count = 0
    total_bytes = 0
    latest_mtime = 0.0

    for root, _dirs, files in os.walk(cam_dir):
        if "sessions" in root.replace("\\", "/").split("/"):
            continue
        for name in files:
            if not name.endswith(".ts"):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            segment_count += 1
            total_bytes += st.st_size
            if st.st_mtime > latest_mtime:
                latest_mtime = st.st_mtime

    session_count = 0
    sessions_dir = cam_dir / "sessions"
    if sessions_dir.is_dir():
        try:
            session_count = sum(1 for d in os.scandir(sessions_dir) if d.is_dir())
        except OSError:
            session_count = 0

    if segment_count == 0:
        return {**_empty_session_stats(), "session_count": session_count}

    return {
        "segment_count": segment_count,
        "total_bytes": total_bytes,
        "storage_used_gb": round(total_bytes / 1e9, 4),
        "latest_segment_time": datetime.fromtimestamp(
            latest_mtime, tz=timezone.utc
        ).isoformat(),
        "session_count": session_count,
    }


async def _camera_meta() -> Tuple[Dict[str, str], Dict[str, dict]]:
    names: Dict[str, str] = {}
    meta: Dict[str, dict] = {}
    async for cam in camera_collection.find({}):
        cid = str(cam["_id"])
        names[cid] = cam.get("name") or cid
        meta[cid] = {
            "site": (cam.get("site") or "").strip(),
            "building": (cam.get("building") or "").strip(),
            "floor": (cam.get("floor_group") or cam.get("floor") or "").strip(),
            "camera_group": (cam.get("camera_group") or "").strip(),
        }
    return names, meta


def _rollup_recording_by_location(
    cameras: List[dict],
    meta: Dict[str, dict],
) -> List[dict]:
    sites: Dict[str, dict] = {}

    for cam in cameras:
        cid = cam["camera_id"]
        loc = meta.get(cid) or {}
        site = loc.get("site") or "Unassigned"
        building = loc.get("building") or "Unassigned"
        floor = loc.get("floor") or "Unassigned"
        recording = bool(cam.get("is_recording"))

        if site not in sites:
            sites[site] = {"site": site, "buildings": {}, "total": 0, "recording": 0}
        site_row = sites[site]
        site_row["total"] += 1
        if recording:
            site_row["recording"] += 1

        bkey = f"{site}::{building}"
        if bkey not in site_row["buildings"]:
            site_row["buildings"][bkey] = {
                "site": site,
                "building": building,
                "floors": {},
                "total": 0,
                "recording": 0,
            }
        brow = site_row["buildings"][bkey]
        brow["total"] += 1
        if recording:
            brow["recording"] += 1

        if floor not in brow["floors"]:
            brow["floors"][floor] = {"floor": floor, "total": 0, "recording": 0}
        frow = brow["floors"][floor]
        frow["total"] += 1
        if recording:
            frow["recording"] += 1

    result = []
    for site_name in sorted(sites.keys()):
        site_row = sites[site_name]
        buildings = []
        for bkey in sorted(site_row["buildings"].keys()):
            brow = site_row["buildings"][bkey]
            floors = [brow["floors"][k] for k in sorted(brow["floors"].keys())]
            buildings.append(
                {
                    "site": brow["site"],
                    "building": brow["building"],
                    "total": brow["total"],
                    "recording": brow["recording"],
                    "floors": floors,
                }
            )
        result.append(
            {
                "site": site_name,
                "total": site_row["total"],
                "recording": site_row["recording"],
                "buildings": buildings,
            }
        )
    return result


async def _camera_names() -> Dict[str, str]:
    names, _meta = await _camera_meta()
    return names


async def _stats_for_folder(folder: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_FS_EXECUTOR, _camera_filesystem_stats, folder)


async def _growth_rates_by_camera() -> Dict[str, float]:
    """bytes/hour per camera from live snapshot or latest status log."""
    rates: Dict[str, float] = {}
    for camera_id, snap in _prev_snapshot.items():
        bph = snap.get("bytes_per_hour")
        if bph:
            rates[camera_id] = float(bph)

    try:
        pipeline = [
            {"$sort": {"at": -1}},
            {
                "$group": {
                    "_id": "$camera_id",
                    "bytes_per_hour": {"$first": "$bytes_per_hour"},
                }
            },
        ]
        async for doc in recording_status_logs_collection.aggregate(pipeline):
            cid = doc["_id"]
            bph = doc.get("bytes_per_hour")
            if cid and cid not in rates and bph:
                rates[cid] = float(bph)
    except Exception as e:
        logger.debug(f"[STORAGE] growth rate lookup failed: {e}")

    return rates


def _estimate_days_remaining(free_gb: float, gb_per_day: float) -> Optional[float]:
    if not gb_per_day or gb_per_day <= 0:
        return None
    return round(free_gb / gb_per_day, 1)


# Substream ~512 Kbps ≈ 5.4 GB/day per camera (planning fallback)
_DEFAULT_GB_PER_DAY_MAIN = 40.0
_DEFAULT_GB_PER_DAY_SUB = 5.4
_DEFAULT_GB_PER_DAY_PER_CAMERA = (
    _DEFAULT_GB_PER_DAY_MAIN if RECORDING_STREAM == "main" else _DEFAULT_GB_PER_DAY_SUB
)


async def _projected_gb_per_day(growth: Dict[str, float], camera_rows: list) -> float:
    """Sum latest known per-camera growth; fallback for scheduled/pilot cameras."""
    total = 0.0
    for bph in growth.values():
        if bph and bph > 0:
            total += bph * 24 / 1e9

    if total > 0:
        return round(total, 2)

    from app.core.database import get_pilot_recording

    pilot = await get_pilot_recording()
    if pilot and pilot.get("status") == "active":
        n = len(pilot.get("camera_ids", []))
        if n:
            return round(n * _DEFAULT_GB_PER_DAY_PER_CAMERA, 2)

    recording_count = sum(1 for c in camera_rows if c.get("is_recording"))
    if recording_count:
        return round(recording_count * _DEFAULT_GB_PER_DAY_PER_CAMERA, 2)

    with_history = sum(1 for c in camera_rows if c.get("segment_count", 0) > 0)
    if with_history:
        return round(with_history * _DEFAULT_GB_PER_DAY_PER_CAMERA, 2)

    return 0.0


async def get_storage_dashboard(*, summary_only: bool = False) -> dict:
    disk = _disk_usage_for_recordings()
    names, meta = await _camera_meta()
    growth = await _growth_rates_by_camera()

    camera_ids = set(names.keys())
    legacy_mapped = await mapped_legacy_folder_ids()
    if not summary_only and RECORDINGS_DIR.is_dir():
        for child in RECORDINGS_DIR.iterdir():
            if child.is_dir() and child.name not in legacy_mapped:
                camera_ids.add(child.name)

    cameras: List[dict] = []
    total_recordings_bytes = 0

    recording_flags = {}
    for camera_id in camera_ids:
        if camera_id in names:
            recording_flags[camera_id] = await is_camera_recording(camera_id)

    for camera_id in sorted(camera_ids, key=lambda c: names.get(c, c)):
        if summary_only:
            stats = {**_empty_session_stats(), "session_count": 0}
        elif camera_id in names:
            uid = await resolve_camera_uid(camera_id)
            folders = await storage_folder_keys_for_uid(uid or camera_id)
            stats = {"segment_count": 0, "total_bytes": 0, "storage_used_gb": 0.0, "latest_segment_time": None, "session_count": 0}
            if folders:
                parts = await asyncio.gather(*[_stats_for_folder(f) for f in folders])
                for part in parts:
                    if part.get("segment_count", 0) >= stats.get("segment_count", 0):
                        stats = part
        else:
            stats = await _stats_for_folder(camera_id)

        recording = recording_flags.get(camera_id, False)
        bph = growth.get(camera_id)
        if camera_id in names:
            uid = await resolve_camera_uid(camera_id)
            if uid:
                bph = bph or growth.get(uid)
        gb_per_day = round(bph * 24 / 1e9, 3) if bph else None
        total_recordings_bytes += stats["total_bytes"]

        loc = meta.get(camera_id, {})
        cameras.append(
            {
                "camera_id": camera_id,
                "camera_name": names.get(camera_id, camera_id),
                "is_recording": recording,
                "segment_count": stats["segment_count"],
                "session_count": stats.get("session_count", 0),
                "storage_used_gb": stats["storage_used_gb"],
                "total_bytes": stats["total_bytes"],
                "latest_segment_time": stats["latest_segment_time"],
                "bytes_per_hour": int(bph) if bph else None,
                "gb_per_day_estimate": gb_per_day,
                "site": loc.get("site") or "",
                "building": loc.get("building") or "",
                "floor": loc.get("floor") or "",
            }
        )

    cameras.sort(key=lambda c: c["storage_used_gb"], reverse=True)

    total_gb_per_day = await _projected_gb_per_day(growth, cameras)
    system_days = _estimate_days_remaining(disk["disk_free_gb"], total_gb_per_day)

    for cam in cameras:
        cam_gb = cam.get("gb_per_day_estimate") or (
            _DEFAULT_GB_PER_DAY_PER_CAMERA if cam.get("is_recording") else None
        )
        cam["estimated_days_remaining"] = _estimate_days_remaining(
            disk["disk_free_gb"], cam_gb or 0
        )

    recordings_gb = round(total_recordings_bytes / 1e9, 4)

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "recordings_root": str(RECORDINGS_DIR),
        "stream_profile": recording_stream_profile(),
        "recording": get_recording_stream_info(),
        "retention": get_retention_policy(),
        "disk": disk,
        "summary": {
            "recordings_storage_gb": recordings_gb,
            "recordings_storage_bytes": total_recordings_bytes,
            "camera_count": len(cameras),
            "cameras_recording": sum(1 for c in cameras if c["is_recording"]),
            "total_segments": sum(c["segment_count"] for c in cameras),
            "combined_gb_per_day": total_gb_per_day if total_gb_per_day else None,
            "estimated_days_remaining": system_days,
            "days_remaining_formula": (
                f"{disk['disk_free_gb']} GB ÷ {total_gb_per_day} GB/day"
                if total_gb_per_day and system_days is not None
                else None
            ),
        },
        "cameras": cameras,
        "recordingByLocation": _rollup_recording_by_location(cameras, meta),
        "summary_only": summary_only,
    }
