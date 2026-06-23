"""Minute status logs + MongoDB metadata + bytes/hour disk growth."""

import logging
from datetime import datetime, timezone
from typing import Dict

from app.core.database import (
    update_recording_session,
    insert_recording_status_log,
    recording_sessions_collection,
)
from app.services.video_recording import (
    ACTIVE_RECORDINGS,
    _session_stats,
    is_camera_recording,
    reconcile_stale_db_sessions,
    sync_session_stats_to_db,
    session_storage_dir,
    storage_folder_from_path,
)

logger = logging.getLogger(__name__)

# camera_id -> {total_bytes, at}
_prev_snapshot: Dict[str, dict] = {}


def _bytes_per_hour(camera_id: str, total_bytes: int) -> float | None:
    prev = _prev_snapshot.get(camera_id)
    now = datetime.now(timezone.utc)
    if not prev:
        _prev_snapshot[camera_id] = {"total_bytes": total_bytes, "at": now}
        return None
    elapsed = (now - prev["at"]).total_seconds()
    if elapsed < 30:
        return prev.get("bytes_per_hour")
    delta = max(0, total_bytes - prev["total_bytes"])
    rate = delta / elapsed * 3600
    _prev_snapshot[camera_id] = {"total_bytes": total_bytes, "at": now, "bytes_per_hour": rate}
    return rate


def _stats_meta(stats: dict, *, ffmpeg_alive: bool | None = None) -> dict:
    """Build MongoDB payload from filesystem stats."""
    meta = {
        "segment_count": stats["segment_count"],
        "total_bytes": stats["total_bytes"],
        "storage_used_gb": stats["storage_used_gb"],
        "latest_segment_time": stats["latest_segment_time"],
        "last_stats_at": datetime.now(timezone.utc).isoformat(),
    }
    if ffmpeg_alive is not None:
        meta["ffmpeg_alive"] = ffmpeg_alive
    return meta


async def sync_orphan_recording_stats_from_disk() -> int:
    """Sync filesystem stats for DB rows marked recording but not in ACTIVE_RECORDINGS."""
    live_ids = {entry["session_id"] for entry in ACTIVE_RECORDINGS.values()}
    synced = 0
    async for doc in recording_sessions_collection.find({"status": "recording"}):
        session_id = str(doc["_id"])
        if session_id in live_ids:
            continue
        await sync_session_stats_to_db(doc["camera_id"], session_id)
        synced += 1
    return synced


async def log_active_recording_stats() -> list:
    """Update MongoDB + log every minute for each recording camera."""
    await reconcile_stale_db_sessions()
    await sync_orphan_recording_stats_from_disk()

    reports = []
    for camera_id, entry in list(ACTIVE_RECORDINGS.items()):
        recorder = entry["recorder"]
        session_id = entry["session_id"]
        if not recorder.is_recording:
            continue

        stats = _session_stats(recorder.session_dir)
        proc = recorder.recording_process
        ffmpeg_alive = proc is not None and proc.returncode is None

        bph = _bytes_per_hour(camera_id, stats["total_bytes"])
        gb_per_day = (bph * 24 / 1e9) if bph else None

        meta = _stats_meta(stats, ffmpeg_alive=ffmpeg_alive)
        if bph is not None:
            meta["bytes_per_hour"] = int(bph)
            meta["gb_per_day_estimate"] = round(gb_per_day, 3) if gb_per_day else None

        await update_recording_session(session_id, meta)
        await insert_recording_status_log(
            {
                "camera_id": camera_id,
                "session_id": session_id,
                "at": meta["last_stats_at"],
                **meta,
            }
        )

        rate_str = f"{bph / 1e6:.2f} MB/h" if bph else "measuring…"
        day_str = f"{gb_per_day:.2f} GB/day" if gb_per_day else ""
        logger.info(
            f"[RECORDING][status] {camera_id} session={session_id[:8]}… "
            f"segments={stats['segment_count']} disk={stats['storage_used_gb']:.3f} GB "
            f"latest={stats['latest_segment_time'] or 'none'} "
            f"growth={rate_str} {day_str} ffmpeg={'ok' if ffmpeg_alive else 'DOWN'}"
        )

        reports.append(
            {
                "camera_id": camera_id,
                "session_id": session_id,
                "is_recording": await is_camera_recording(camera_id),
                **meta,
            }
        )

    return reports


async def get_disk_summary() -> dict:
    from app.services.recording_config import RECORDING_RETENTION_SECONDS, recording_stream_profile

    reports = []
    for camera_id, entry in list(ACTIVE_RECORDINGS.items()):
        recorder = entry["recorder"]
        if not recorder.is_recording:
            continue
        stats = _session_stats(recorder.session_dir)
        bph = _prev_snapshot.get(camera_id, {}).get("bytes_per_hour")
        reports.append(
            {
                "camera_id": camera_id,
                "session_id": entry["session_id"],
                "segment_count": stats["segment_count"],
                "total_bytes": stats["total_bytes"],
                "storage_used_gb": stats["storage_used_gb"],
                "latest_segment_time": stats["latest_segment_time"],
                "bytes_per_hour": int(bph) if bph else None,
                "gb_per_day_estimate": round(bph * 24 / 1e9, 2) if bph else None,
            }
        )
    total_bytes = sum(r.get("total_bytes", 0) for r in reports)
    total_bph = sum(r.get("bytes_per_hour", 0) or 0 for r in reports)
    return {
        "stream_profile": recording_stream_profile(),
        "retention_hours": round(RECORDING_RETENTION_SECONDS / 3600, 2),
        "active_cameras": len(reports),
        "total_bytes": total_bytes,
        "storage_used_gb": round(total_bytes / 1e9, 4),
        "combined_bytes_per_hour": int(total_bph) if total_bph else None,
        "combined_gb_per_day_estimate": round(total_bph * 24 / 1e9, 2) if total_bph else None,
        "cameras": reports,
    }


async def backfill_all_session_stats_from_disk(*, limit: int = 200) -> int:
    """One-shot backfill: sync filesystem stats for recent sessions (stopped + recording)."""
    updated = 0
    cursor = recording_sessions_collection.find({}).sort("started_at", -1).limit(limit)
    async for doc in cursor:
        session_id = str(doc["_id"])
        folder = storage_folder_from_path(
            doc.get("storage_path") or doc.get("file_path"),
            doc.get("camera_id") or "",
        )
        if not folder:
            continue
        session_dir = session_storage_dir(folder, session_id)
        stats = _session_stats(session_dir)
        await update_recording_session(session_id, stats)
        updated += 1
    return updated
