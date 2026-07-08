"""Per-camera recording health for NVR operations dashboard."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from app.core.database import camera_collection, get_active_recording_session
from app.services.recording_config import RECORDING_SEGMENT_SECONDS
from app.services.camera_identity import make_camera_uid, storage_folder_keys_for_uid
from app.services.storage_dashboard import _camera_filesystem_stats
from app.services.video_recording import ACTIVE_RECORDINGS

logger = logging.getLogger(__name__)

# Segment considered stale after 2× segment length + 2 min buffer
_STALE_BUFFER_SEC = 120
_CACHE_TTL_SEC = 12.0
_cache: Dict[str, Any] = {"expires_at": 0.0, "payload": None}


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


def _folder_keys_from_cam(cam: dict) -> list[str]:
    """Derive storage folders from an already-loaded camera doc (no extra Mongo round-trips)."""
    camera_id = str(cam["_id"])
    uid = cam.get("camera_uid") or make_camera_uid((cam.get("ip_address") or "").strip()) or camera_id
    keys = [str(uid), camera_id]
    stored = cam.get("recording_storage_id")
    if stored and str(stored) not in keys:
        keys.append(str(stored))
    return list(dict.fromkeys(keys))


async def get_recording_health(
    scheduled_camera_ids: Optional[Set[str]] = None,
    *,
    force: bool = False,
) -> dict:
    """Build health row per camera for monitoring UI.

    Fast path: when nothing is recording/scheduled, skip filesystem walks.
    Cached for a few seconds so Storage UI polling at 15s stays responsive with 575+ cameras.
    """
    now_mono = time.monotonic()
    if not force and _cache["payload"] is not None and now_mono < float(_cache["expires_at"]):
        return _cache["payload"]

    now = datetime.now(timezone.utc)
    scheduled = scheduled_camera_ids or set()
    active_ids = set(ACTIVE_RECORDINGS.keys())
    interesting = scheduled | active_ids
    scan_disk = bool(interesting)

    cameras = []
    counts = {"healthy": 0, "warning": 0, "reconnecting": 0, "offline": 0, "idle": 0}

    projection = {
        "name": 1,
        "camera_uid": 1,
        "ip_address": 1,
        "recording_storage_id": 1,
    }

    async for cam in camera_collection.find({}, projection):
        camera_id = str(cam["_id"])
        name = cam.get("name") or camera_id
        entry = ACTIVE_RECORDINGS.get(camera_id)
        recording = bool(entry and entry["recorder"].is_recording)
        ff_alive = False
        if entry:
            proc = entry["recorder"].recording_process
            ff_alive = proc is not None and proc.returncode is None

        disk_stats = {"segment_count": 0, "latest_segment_time": None}
        latest_seg = None
        active_session = None
        last_recording = None

        if scan_disk and (camera_id in interesting or recording):
            folders = _folder_keys_from_cam(cam)
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

    payload = {
        "updated_at": now.isoformat(),
        "summary": {
            "total": len(cameras),
            "recording": sum(1 for c in cameras if c["recording_status"] == "Recording"),
            **counts,
        },
        "cameras": cameras,
    }
    _cache["payload"] = payload
    _cache["expires_at"] = now_mono + _CACHE_TTL_SEC
    return payload
