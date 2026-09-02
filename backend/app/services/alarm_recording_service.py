"""Alarm-triggered temporary recording — no schedule side effects."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.database import get_active_recording_session, update_recording_session
from app.services.alarm_constants import RECORDING_ACTION_STATUSES
from app.services.recording_config import RecordingEngineDisabled, is_recording_engine_enabled
from app.services import recording_schedule_store as recording_sched
from app.services.video_recording import is_camera_recording, start_camera_recording, stop_camera_recording

logger = logging.getLogger(__name__)

_alarm_owned: dict[str, dict[str, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _lock_for(camera_id: str) -> asyncio.Lock:
    if camera_id not in _locks:
        _locks[camera_id] = asyncio.Lock()
    return _locks[camera_id]


def is_alarm_owned_recording(camera_id: str) -> bool:
    """True while an alarm action owns an active temporary recording for this camera."""
    cid = str(camera_id or "").strip()
    return cid in _alarm_owned


def get_alarm_owned_session_id(camera_id: str) -> Optional[str]:
    entry = _alarm_owned.get(str(camera_id or "").strip())
    if not entry:
        return None
    return str(entry.get("session_id") or "") or None


def _cancel_stop_task(entry: dict[str, Any]) -> None:
    task = entry.get("stop_task")
    if task and not task.done():
        task.cancel()


def _schedule_auto_stop(camera_id: str, session_id: str, auto_stop_at: datetime) -> asyncio.Task:
    async def _worker() -> None:
        try:
            while True:
                entry = _alarm_owned.get(camera_id)
                if not entry or entry.get("session_id") != session_id:
                    return
                wait_until = entry.get("auto_stop_at") or auto_stop_at
                delay = (wait_until - _utcnow()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                entry = _alarm_owned.get(camera_id)
                if not entry or entry.get("session_id") != session_id:
                    return
                if _utcnow() < entry.get("auto_stop_at", auto_stop_at):
                    continue
                break
            if await is_camera_recording(camera_id):
                await stop_camera_recording(camera_id)
            _alarm_owned.pop(camera_id, None)
            logger.info(
                "[alarm-recording] Auto-stopped alarm-owned session camera=%s session=%s",
                camera_id,
                session_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[alarm-recording] Auto-stop failed camera=%s session=%s: %s",
                camera_id,
                session_id,
                exc,
                exc_info=True,
            )

    return asyncio.create_task(_worker())


async def _apply_alarm_session_metadata(
    session_id: str,
    *,
    event_id: str,
    rule_id: str,
    source_type: str,
    auto_stop_at: datetime,
) -> None:
    await update_recording_session(
        session_id,
        {
            "start_reason": "alarm",
            "trigger_type": "alarm",
            "event_id": event_id,
            "rule_id": rule_id,
            "source_type": source_type,
            "auto_stop_at": _iso(auto_stop_at),
        },
    )


def _result(
    *,
    recording_status: str,
    recording_session_id: Optional[str] = None,
) -> dict[str, Any]:
    if recording_status not in RECORDING_ACTION_STATUSES:
        recording_status = "failed"
    out: dict[str, Any] = {"recording_status": recording_status}
    if recording_session_id:
        out["recording_session_id"] = recording_session_id
    return out


async def start_alarm_triggered_recording(
    camera_id: str,
    *,
    event_id: str,
    rule_id: str,
    source_type: str,
    duration_seconds: int,
) -> dict[str, Any]:
    """Start or extend alarm-owned recording without changing the normal schedule."""
    cid = str(camera_id or "").strip()
    if not cid:
        return _result(recording_status="failed")

    if not is_recording_engine_enabled():
        return _result(recording_status="engine_disabled")

    if not recording_sched.master_enabled:
        return _result(recording_status="master_disabled")

    duration = max(1, int(duration_seconds))
    auto_stop_at = _utcnow() + timedelta(seconds=duration)

    async with _lock_for(cid):
        if await is_camera_recording(cid):
            active = await get_active_recording_session(cid)
            session_id = str((active or {}).get("id") or "")
            if not session_id:
                return _result(recording_status="failed")

            if is_alarm_owned_recording(cid):
                entry = _alarm_owned[cid]
                if entry.get("session_id") == session_id:
                    _cancel_stop_task(entry)
                    entry["auto_stop_at"] = auto_stop_at
                    entry["stop_task"] = _schedule_auto_stop(cid, session_id, auto_stop_at)
                    await update_recording_session(session_id, {"auto_stop_at": _iso(auto_stop_at)})
                    logger.info(
                        "[alarm-recording] Extended alarm session camera=%s session=%s until=%s",
                        cid,
                        session_id,
                        _iso(auto_stop_at),
                    )
                    return _result(recording_status="extended", recording_session_id=session_id)

            logger.info(
                "[alarm-recording] Reusing existing non-alarm session camera=%s session=%s",
                cid,
                session_id,
            )
            return _result(recording_status="already_recording", recording_session_id=session_id)

        try:
            session = await start_camera_recording(cid)
        except RecordingEngineDisabled:
            return _result(recording_status="engine_disabled")
        except Exception as exc:
            logger.error("[alarm-recording] Start failed camera=%s: %s", cid, exc, exc_info=True)
            return _result(recording_status="failed")

        session_id = str(session.get("id") or "")
        if not session_id:
            return _result(recording_status="failed")

        await _apply_alarm_session_metadata(
            session_id,
            event_id=event_id,
            rule_id=rule_id,
            source_type=source_type,
            auto_stop_at=auto_stop_at,
        )

        stop_task = _schedule_auto_stop(cid, session_id, auto_stop_at)
        _alarm_owned[cid] = {
            "session_id": session_id,
            "event_id": event_id,
            "rule_id": rule_id,
            "auto_stop_at": auto_stop_at,
            "stop_task": stop_task,
        }
        logger.info(
            "[alarm-recording] Started alarm session camera=%s session=%s until=%s",
            cid,
            session_id,
            _iso(auto_stop_at),
        )
        return _result(recording_status="started", recording_session_id=session_id)


def reset_alarm_recording_for_tests() -> None:
    """Clear in-memory alarm ownership state between tests."""
    for entry in _alarm_owned.values():
        _cancel_stop_task(entry)
    _alarm_owned.clear()
    _locks.clear()
