"""Recording schedule state (memory + MongoDB) shared across routes and services."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List

from app.core.database import camera_collection, database

logger = logging.getLogger(__name__)

_settings_collection = database.get_collection("system_settings")
_SETTINGS_ID = "recording"

# Shared in-process state — updated by routes and camera add flow.
# Recording is opt-in per camera (manual toggle only).
master_enabled: bool = False
recording_schedule: Dict[str, bool] = {}


async def load_recording_settings() -> None:
    """Load master flag + schedule from MongoDB into module globals."""
    global master_enabled, recording_schedule
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if not doc:
        master_enabled = False
        recording_schedule = {}
        return
    master_enabled = bool(doc.get("master_enabled", False))
    raw = doc.get("schedule") or {}
    recording_schedule = {str(k): bool(v) for k, v in raw.items()}


async def save_recording_settings() -> None:
    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {
            "$set": {
                "master_enabled": master_enabled is True,
                "schedule": {str(k): bool(v) for k, v in recording_schedule.items()},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=True,
    )


def _sync_master_with_schedule() -> None:
    """Master switch follows whether any camera is scheduled to record."""
    global master_enabled
    master_enabled = any(recording_schedule.values())


async def sync_schedule_with_cameras(*, default_new: bool | None = None) -> None:
    """
    Ensure every active camera has a schedule entry.
    New cameras default to off unless explicitly passed default_new=True.
    """
    global recording_schedule
    if default_new is None:
        default_new = False

    active_ids: set[str] = set()
    async for cam in camera_collection.find({}):
        if cam.get("is_active") is False:
            continue
        active_ids.add(str(cam["_id"]))

    updated = {cid: recording_schedule[cid] for cid in recording_schedule if cid in active_ids}
    for cid in active_ids:
        if cid not in updated:
            updated[cid] = default_new
            logger.info("[RECORDING] Added camera %s to schedule (enabled=%s)", cid, default_new)

    removed = set(recording_schedule.keys()) - active_ids
    for cid in removed:
        logger.info("[RECORDING] Removed deleted camera %s from schedule", cid)

    recording_schedule = updated
    _sync_master_with_schedule()


async def stop_all_scheduled_recording(*, persist: bool = True) -> dict:
    """Stop FFmpeg for all cameras and clear schedule (opt-in mode)."""
    global master_enabled, recording_schedule

    from app.services.recording_pilot import stop_pilot
    from app.services.video_recording import cleanup_all_recordings, is_camera_recording, stop_camera_recording

    pilot = await stop_pilot(reason="stop_all")

    stopped: List[str] = []
    candidate_ids = set(recording_schedule.keys())
    async for cam in camera_collection.find({}):
        candidate_ids.add(str(cam["_id"]))

    for cid in candidate_ids:
        try:
            if await is_camera_recording(cid):
                await stop_camera_recording(cid)
                stopped.append(cid)
        except Exception as exc:
            logger.error("[RECORDING] Error stopping %s: %s", cid, exc)

    await cleanup_all_recordings()

    recording_schedule = {cid: False for cid in recording_schedule}
    master_enabled = False

    if persist:
        await save_recording_settings()
        await _settings_collection.update_one(
            {"_id": _SETTINGS_ID},
            {"$set": {"opt_in_recording": True}},
            upsert=True,
        )

    logger.info("[RECORDING] Stopped all cameras (%s active stopped)", len(stopped))
    return {"stopped": stopped, "pilot": pilot, "master_enabled": False}


async def bootstrap_recording_schedule() -> None:
    """Load persisted schedule, sync with cameras, apply one-time opt-in migration."""
    await load_recording_settings()
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID}) or {}

    if not doc.get("opt_in_recording"):
        logger.info("[RECORDING] Applying opt-in recording mode (stop all, manual toggle only)")
        await stop_all_scheduled_recording(persist=True)
    else:
        await sync_schedule_with_cameras()
        await save_recording_settings()

    enabled = sum(1 for v in recording_schedule.values() if v)
    logger.info(
        "[RECORDING] Schedule ready: master=%s, cameras_scheduled=%s/%s",
        master_enabled,
        enabled,
        len(recording_schedule),
    )


async def register_camera_for_recording(camera_id: str) -> None:
    """Add a new camera to schedule (off by default)."""
    global recording_schedule
    cid = str(camera_id)
    if cid not in recording_schedule:
        recording_schedule[cid] = False
        await save_recording_settings()
        logger.info("[RECORDING] Registered new camera %s (enabled=False)", cid)


async def apply_schedule_update(incoming: Dict[str, bool]) -> None:
    """Merge schedule flags for active cameras (never drop cameras missing from payload)."""
    global recording_schedule
    await sync_schedule_with_cameras()
    normalized = {str(k): bool(v) for k, v in incoming.items()}
    for cid in list(recording_schedule.keys()):
        if cid in normalized:
            recording_schedule[cid] = normalized[cid]
    _sync_master_with_schedule()
    await save_recording_settings()


def set_camera_recording(camera_id: str, enabled: bool) -> None:
    """Update one camera in schedule and sync master flag."""
    global recording_schedule
    cid = str(camera_id)
    recording_schedule[cid] = bool(enabled)
    _sync_master_with_schedule()

