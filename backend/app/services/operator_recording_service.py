"""Operator duration-based recording — temporary clip with auto-stop.

Does not flip the continuous schedule. Monitor must leave these sessions alone
until auto-stop (same ownership idea as alarm recordings).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.database import get_active_recording_session, update_recording_session
from app.services.alarm_constants import (
    RECORDING_DURATION_MAX_SECONDS,
    RECORDING_DURATION_MIN_SECONDS,
)
from app.services.recording_config import RecordingEngineDisabled, is_recording_engine_enabled
from app.services.video_recording import is_camera_recording, start_camera_recording, stop_camera_recording

logger = logging.getLogger(__name__)

_duration_owned: dict[str, dict[str, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _lock_for(camera_id: str) -> asyncio.Lock:
    if camera_id not in _locks:
        _locks[camera_id] = asyncio.Lock()
    return _locks[camera_id]


def is_operator_duration_owned(camera_id: str) -> bool:
    cid = str(camera_id or "").strip()
    return cid in _duration_owned


def get_operator_duration_session_id(camera_id: str) -> Optional[str]:
    entry = _duration_owned.get(str(camera_id or "").strip())
    if not entry:
        return None
    return str(entry.get("session_id") or "") or None


def get_operator_auto_stop_at(camera_id: str) -> Optional[datetime]:
    entry = _duration_owned.get(str(camera_id or "").strip())
    if not entry:
        return None
    return entry.get("auto_stop_at")


def clamp_duration_seconds(value: int) -> int:
    return max(RECORDING_DURATION_MIN_SECONDS, min(RECORDING_DURATION_MAX_SECONDS, int(value)))


def _cancel_stop_task(entry: dict[str, Any]) -> None:
    task = entry.get("stop_task")
    if task and not task.done():
        task.cancel()


def release_operator_duration_ownership(camera_id: str) -> None:
    cid = str(camera_id or "").strip()
    entry = _duration_owned.pop(cid, None)
    if entry:
        _cancel_stop_task(entry)


def _schedule_auto_stop(camera_id: str, session_id: str, auto_stop_at: datetime) -> asyncio.Task:
    async def _worker() -> None:
        try:
            while True:
                entry = _duration_owned.get(camera_id)
                if not entry or entry.get("session_id") != session_id:
                    return
                wait_until = entry.get("auto_stop_at") or auto_stop_at
                delay = (wait_until - _utcnow()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                entry = _duration_owned.get(camera_id)
                if not entry or entry.get("session_id") != session_id:
                    return
                if _utcnow() < entry.get("auto_stop_at", auto_stop_at):
                    continue
                break
            if await is_camera_recording(camera_id):
                await stop_camera_recording(camera_id)
            _duration_owned.pop(camera_id, None)
            logger.info(
                "[operator-recording] Auto-stopped duration session camera=%s session=%s",
                camera_id,
                session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[operator-recording] Auto-stop failed camera=%s session=%s: %s",
                camera_id,
                session_id,
                exc,
                exc_info=True,
            )

    return asyncio.create_task(_worker())


async def start_operator_duration_recording(
    camera_id: str,
    *,
    duration_seconds: int,
) -> dict[str, Any]:
    """Start or extend a temporary operator recording with auto-stop."""
    cid = str(camera_id or "").strip()
    if not cid:
        return {"ok": False, "status": "failed", "error": "camera_id required"}

    if not is_recording_engine_enabled():
        return {"ok": False, "status": "engine_disabled", "error": "Recording engine is disabled"}

    duration = clamp_duration_seconds(duration_seconds)
    auto_stop_at = _utcnow() + timedelta(seconds=duration)

    async with _lock_for(cid):
        if await is_camera_recording(cid):
            active = await get_active_recording_session(cid)
            session_id = str((active or {}).get("id") or "")
            if not session_id:
                return {"ok": False, "status": "failed", "error": "Active recording has no session"}

            if is_operator_duration_owned(cid):
                entry = _duration_owned[cid]
                if entry.get("session_id") == session_id:
                    _cancel_stop_task(entry)
                    entry["auto_stop_at"] = auto_stop_at
                    entry["stop_task"] = _schedule_auto_stop(cid, session_id, auto_stop_at)
                    await update_recording_session(session_id, {"auto_stop_at": _iso(auto_stop_at)})
                    return {
                        "ok": True,
                        "status": "extended",
                        "session_id": session_id,
                        "auto_stop_at": _iso(auto_stop_at),
                        "duration_seconds": duration,
                    }

            # Continuous/alarm session already running — do not take over.
            return {
                "ok": True,
                "status": "already_recording",
                "session_id": session_id,
                "error": "Camera is already recording",
            }

        try:
            session = await start_camera_recording(cid)
        except RecordingEngineDisabled:
            return {"ok": False, "status": "engine_disabled", "error": "Recording engine is disabled"}
        except Exception as exc:
            logger.error("[operator-recording] Start failed camera=%s: %s", cid, exc, exc_info=True)
            return {"ok": False, "status": "failed", "error": str(exc)}

        session_id = str(session.get("id") or "")
        if not session_id:
            return {"ok": False, "status": "failed", "error": "No session id"}

        await update_recording_session(
            session_id,
            {
                "start_reason": "operator_duration",
                "trigger_type": "operator",
                "auto_stop_at": _iso(auto_stop_at),
                "duration_seconds": duration,
            },
        )

        stop_task = _schedule_auto_stop(cid, session_id, auto_stop_at)
        _duration_owned[cid] = {
            "session_id": session_id,
            "auto_stop_at": auto_stop_at,
            "stop_task": stop_task,
        }
        logger.info(
            "[operator-recording] Started duration session camera=%s session=%s until=%s",
            cid,
            session_id,
            _iso(auto_stop_at),
        )
        return {
            "ok": True,
            "status": "started",
            "session_id": session_id,
            "session": session,
            "auto_stop_at": _iso(auto_stop_at),
            "duration_seconds": duration,
        }


def is_temporary_owned_recording(camera_id: str) -> bool:
    """True when alarm or operator-duration owns the active session (monitor must not stop)."""
    from app.services.alarm_recording_service import is_alarm_owned_recording

    return is_alarm_owned_recording(camera_id) or is_operator_duration_owned(camera_id)


def reset_operator_recording_for_tests() -> None:
    for entry in _duration_owned.values():
        _cancel_stop_task(entry)
    _duration_owned.clear()
    _locks.clear()
