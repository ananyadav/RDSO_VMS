"""
Phase 1 pilot: record exactly 2 cameras for 4 days / 96 hours (configurable).
Persists to MongoDB; auto-stops when time expires; survives backend restart.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.core.database import (
    get_pilot_recording,
    list_cameras_for_pilot,
    save_pilot_recording,
    camera_collection,
)
from app.services.recording_config import (
    RECORDING_RETENTION_SECONDS,
    recording_stream_profile,
)
from app.services.storage_settings_store import get_effective_recordings_dir
from app.services.video_recording import (
    start_camera_recording,
    stop_camera_recording,
    is_camera_recording,
)
from bson import ObjectId

PILOT_HOURS = float(os.getenv("PILOT_RECORDING_HOURS", "96"))  # 4 days default
PILOT_COUNT = int(os.getenv("PILOT_RECORDING_COUNT", "2"))
STREAM_PROFILE = recording_stream_profile()


async def _camera_names(camera_ids: List[str]) -> List[str]:
    names = []
    for cid in camera_ids:
        doc = await camera_collection.find_one({"_id": ObjectId(cid)})
        names.append(doc.get("name", cid) if doc else cid)
    return names


async def start_pilot(
    camera_ids: Optional[List[str]] = None,
    hours: float = PILOT_HOURS,
) -> dict:
    """Start 24h pilot on up to 2 cameras. Does not remove or modify other cameras."""
    existing = await get_pilot_recording()
    if existing and existing.get("status") == "active":
        ends = datetime.fromisoformat(existing["ends_at"].replace("Z", "+00:00"))
        same_cameras = not camera_ids or set(camera_ids) == set(existing.get("camera_ids", []))
        if ends > datetime.now(timezone.utc) and same_cameras:
            logging.info("[PILOT] Already active — returning existing pilot")
            return existing
        if existing.get("status") == "active":
            await stop_pilot(reason="reconfigure")

    if camera_ids:
        ids = camera_ids[:PILOT_COUNT]
    else:
        picked = await list_cameras_for_pilot(PILOT_COUNT)
        if len(picked) < 1:
            raise ValueError("No cameras in database. Add cameras first.")
        ids = [c["id"] for c in picked]

    if len(ids) > PILOT_COUNT:
        ids = ids[:PILOT_COUNT]

    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(hours=hours)
    names = await _camera_names(ids)

    pilot = await save_pilot_recording(
        {
            "camera_ids": ids,
            "camera_names": names,
            "status": "active",
            "hours": hours,
            "started_at": now.isoformat(),
            "ends_at": ends_at.isoformat(),
            "stream_profile": STREAM_PROFILE,
            "stopped_at": None,
        }
    )

    async def _start_all():
        for cid in ids:
            try:
                await start_camera_recording(cid)
                logging.info(f"[PILOT] Recording started for {cid} until {ends_at.isoformat()}")
            except Exception as e:
                logging.error(f"[PILOT] Failed to start {cid}: {e}")

    asyncio.create_task(_start_all())

    return {
        **pilot,
        "storage_root": str(get_effective_recordings_dir()),
        "message": "Pilot scheduled — FFmpeg starting in background",
        "quality_note": (
            f"Testing mode: {STREAM_PROFILE}, "
            f"retention {RECORDING_RETENTION_SECONDS / 3600:.0f}h, "
            f"segments {os.getenv('RECORDING_HLS_SEGMENT_SECONDS', '300')}s (5 min)"
        ),
    }


async def stop_pilot(reason: str = "manual") -> Optional[dict]:
    pilot = await get_pilot_recording()
    if not pilot or pilot.get("status") != "active":
        return pilot

    for cid in pilot.get("camera_ids", []):
        try:
            if await is_camera_recording(cid):
                await stop_camera_recording(cid)
        except Exception as e:
            logging.error(f"[PILOT] Stop error {cid}: {e}")

    stopped_at = datetime.now(timezone.utc).isoformat()
    return await save_pilot_recording(
        {
            "camera_ids": pilot.get("camera_ids", []),
            "camera_names": pilot.get("camera_names", []),
            "hours": pilot.get("hours"),
            "started_at": pilot.get("started_at"),
            "ends_at": pilot.get("ends_at"),
            "stream_profile": pilot.get("stream_profile"),
            "status": "completed",
            "stopped_at": stopped_at,
            "stop_reason": reason,
        }
    )


async def check_pilot_expiry() -> bool:
    """Returns True if pilot was stopped due to expiry."""
    pilot = await get_pilot_recording()
    if not pilot or pilot.get("status") != "active":
        return False

    ends_at = datetime.fromisoformat(pilot["ends_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) >= ends_at:
        logging.info("[PILOT] 24h window complete — stopping pilot recordings")
        await stop_pilot(reason="expired")
        return True
    return False


async def resume_pilot_on_startup(schedule: dict) -> None:
    """Re-attach schedule flags and restart FFmpeg if pilot still active."""
    pilot = await get_pilot_recording()
    if not pilot or pilot.get("status") != "active":
        return

    ends_at = datetime.fromisoformat(pilot["ends_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) >= ends_at:
        await stop_pilot(reason="expired_on_startup")
        return

    for cid in pilot.get("camera_ids", []):
        schedule[cid] = True
        if not await is_camera_recording(cid):
            try:
                await start_camera_recording(cid)
                logging.info(f"[PILOT] Resumed recording for {cid}")
            except Exception as e:
                logging.error(f"[PILOT] Resume failed {cid}: {e}")

    logging.info(
        f"[PILOT] Active until {pilot['ends_at']} — cameras {pilot.get('camera_names')}"
    )


async def pilot_status() -> dict:
    pilot = await get_pilot_recording()
    if not pilot:
        return {"active": False, "message": "No pilot configured"}
    active = pilot.get("status") == "active"
    remaining_hours = None
    if active and pilot.get("ends_at"):
        ends = datetime.fromisoformat(pilot["ends_at"].replace("Z", "+00:00"))
        delta = ends - datetime.now(timezone.utc)
        remaining_hours = round(max(0, delta.total_seconds() / 3600), 2)
    return {
        "active": active,
        "pilot": pilot,
        "remaining_hours": remaining_hours,
        "storage_root": str(get_effective_recordings_dir()),
        "quality_note": STREAM_PROFILE,
    }
