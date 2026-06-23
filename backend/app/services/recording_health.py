"""Per-camera recording health for NVR operations dashboard."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import camera_collection, get_active_recording_session
from app.services.recording_config import RECORDING_SEGMENT_SECONDS
from app.services.camera_identity import resolve_camera_uid, storage_folder_keys_for_uid
from app.services.storage_dashboard import _camera_filesystem_stats
from app.services.video_recording import ACTIVE_RECORDINGS, is_camera_recording

logger = logging.getLogger(__name__)

# Segment considered stale after 2× segment length + 2 min buffer
_STALE_BUFFER_SEC = 120


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _segment_stale_seconds() -> float:
    return float(RECORDING_SEGMENT_SECONDS) * 2 + _STALE_BUFFER_SEC


def _ffmpeg_alive(camera_id: str) -> bool:
    entry = ACTIVE_RECORDINGS.get(camera_id)
    if not entry:
        return False
    proc = entry["recorder"].recording_process
    return proc is not None and proc.returncode is None


def _classify_health(
    *,
    is_recording: bool,
    ffmpeg_alive: bool,
    latest_segment_time: Optional[str],
    has_schedule: bool,
) -> tuple[str, str]:
    """
    Returns (health, label) — health: healthy | warning | reconnecting | offline | idle
    """
    now = datetime.now(timezone.utc)
    segment_age_sec: Optional[float] = None
    if latest_segment_time:
        seg_dt = _parse_iso(latest_segment_time)
        if seg_dt:
            segment_age_sec = (now - seg_dt).total_seconds()

    stale = _segment_stale_seconds()

    if is_recording:
        if ffmpeg_alive and segment_age_sec is not None and segment_age_sec <= stale:
            return "healthy", "Healthy"
        if ffmpeg_alive and segment_age_sec is None:
            return "warning", "Starting"
        if ffmpeg_alive and segment_age_sec is not None and segment_age_sec > stale:
            return "reconnecting", "Reconnecting"
        return "reconnecting", "Reconnecting"

    if has_schedule and not is_recording:
        return "warning", "Offline"

    if latest_segment_time and segment_age_sec is not None and segment_age_sec <= stale * 3:
        return "idle", "Idle"

    if latest_segment_time:
        return "idle", "Idle"

    return "offline", "Offline"


async def get_recording_health(scheduled_camera_ids: Optional[set] = None) -> dict:
    """Build health row per camera for monitoring UI."""
    now = datetime.now(timezone.utc)
    scheduled = scheduled_camera_ids or set()

    cameras = []
    counts = {"healthy": 0, "warning": 0, "reconnecting": 0, "offline": 0, "idle": 0}

    async for cam in camera_collection.find({}).sort("name", 1):
        camera_id = str(cam["_id"])
        name = cam.get("name") or camera_id
        recording = await is_camera_recording(camera_id)
        ff_alive = _ffmpeg_alive(camera_id) if recording else False
        uid = await resolve_camera_uid(camera_id)
        folders = await storage_folder_keys_for_uid(uid or camera_id)
        disk_stats = {"segment_count": 0, "latest_segment_time": None}
        for folder in folders:
            stats = _camera_filesystem_stats(folder)
            if stats.get("segment_count", 0) > disk_stats.get("segment_count", 0):
                disk_stats = stats
        latest_seg = disk_stats.get("latest_segment_time")

        active_session = await get_active_recording_session(camera_id)
        last_recording = latest_seg
        if active_session:
            last_recording = (
                active_session.get("latest_segment_time")
                or active_session.get("last_stats_at")
                or active_session.get("started_at")
            )

        on_schedule = camera_id in scheduled or recording
        health, label = _classify_health(
            is_recording=recording,
            ffmpeg_alive=ff_alive,
            latest_segment_time=latest_seg,
            has_schedule=on_schedule,
        )
        counts[health] = counts.get(health, 0) + 1

        cameras.append(
            {
                "camera_id": camera_id,
                "camera_name": name,
                "recording_status": "Recording" if recording else "Stopped",
                "ffmpeg_status": "Alive" if ff_alive else ("Down" if recording else "—"),
                "health": health,
                "health_label": label,
                "last_segment_time": latest_seg,
                "last_recording_time": last_recording,
                "segment_count": disk_stats.get("segment_count", 0),
                "session_id": active_session["id"] if active_session else None,
            }
        )

    return {
        "updated_at": now.isoformat(),
        "summary": {
            "total": len(cameras),
            "recording": sum(1 for c in cameras if c["recording_status"] == "Recording"),
            **counts,
        },
        "cameras": cameras,
    }
