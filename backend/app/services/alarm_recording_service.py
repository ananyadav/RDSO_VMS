"""Alarm-triggered temporary recording with pre/post alarm windows.

Post-alarm: FFmpeg session with auto-stop (existing ownership model).
Pre-alarm: real footage frozen from the Instant Replay rolling buffer (when warm),
never faked by starting record only after the trigger.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.database import get_active_recording_session, get_recording_session, update_recording_session
from app.services.alarm_constants import (
    POST_ALARM_DEFAULT_SECONDS,
    PRE_ALARM_DEFAULT_SECONDS,
    RECORDING_ACTION_STATUSES,
)
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_uid import make_camera_uid
from app.services.instant_replay_snapshot import snapshot_instant_replay_pre_alarm
from app.services.recording_config import RecordingEngineDisabled, is_recording_engine_enabled
from app.services import recording_schedule_store as recording_sched
from app.services.storage_settings_store import get_effective_recordings_dir
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


def _camera_uid_from_doc(cam: dict | None, camera_id: str) -> str:
    if not cam:
        return camera_id
    return (
        cam.get("camera_uid")
        or make_camera_uid(cam.get("ip_address") or "")
        or camera_id
    )


async def _session_dir_for(session: dict, camera_id: str) -> Optional[Path]:
    del camera_id  # camera_uid / storage_path on the session are authoritative
    sid = str(session.get("id") or "").strip()
    uid = str(session.get("camera_uid") or "").strip()
    rel = str(session.get("storage_path") or session.get("file_path") or "").strip().replace("\\", "/")

    # Prefer paths that already include the session id.
    if rel and sid and sid in Path(rel).parts:
        path = get_effective_recordings_dir() / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    # start_camera_recording initially stores "{uid}/sessions" then updates DB —
    # the returned meta may still omit the session id; append it.
    if rel and sid and rel.rstrip("/").endswith("/sessions"):
        path = get_effective_recordings_dir() / rel / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    if uid and sid:
        path = get_effective_recordings_dir() / uid / "sessions" / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    if rel:
        path = get_effective_recordings_dir() / rel
        path.mkdir(parents=True, exist_ok=True)
        return path
    return None


async def _freeze_pre_alarm(
    *,
    camera_id: str,
    camera_uid: str,
    pre_alarm_seconds: int,
    event_time: datetime,
    dest_dir: Path,
) -> dict[str, Any]:
    if pre_alarm_seconds <= 0:
        return {
            "status": "skipped",
            "source": None,
            "seconds_requested": 0,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    # Snapshot IR buffer FIRST into a temp dir so rolling delete cannot race.
    tmp = Path(tempfile.mkdtemp(prefix="pre_alarm_"))
    try:
        result = snapshot_instant_replay_pre_alarm(
            camera_uid,
            pre_alarm_seconds=pre_alarm_seconds,
            dest_dir=tmp,
            event_time=event_time,
        )
        if result.get("status") in ("ok", "partial") and any(tmp.glob("*.ts")):
            dest_dir.mkdir(parents=True, exist_ok=True)
            for old in dest_dir.glob("*"):
                try:
                    if old.is_file():
                        old.unlink()
                except OSError:
                    pass
            for item in tmp.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_dir / item.name)
            result = {**result, "path": str(dest_dir)}
            return result
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _apply_alarm_session_metadata(
    session_id: str,
    *,
    event_id: str,
    rule_id: str,
    source_type: str,
    auto_stop_at: datetime,
    pre_alarm_seconds: int,
    post_alarm_seconds: int,
    pre_alarm: dict[str, Any] | None,
) -> None:
    payload: dict[str, Any] = {
        "start_reason": "alarm",
        "trigger_type": "alarm",
        "event_id": event_id,
        "rule_id": rule_id,
        "source_type": source_type,
        "auto_stop_at": _iso(auto_stop_at),
        "pre_alarm_seconds": int(pre_alarm_seconds),
        "post_alarm_seconds": int(post_alarm_seconds),
        # Legacy alias
        "duration_seconds": int(post_alarm_seconds),
    }
    if pre_alarm:
        payload["pre_alarm_status"] = pre_alarm.get("status")
        payload["pre_alarm_source"] = pre_alarm.get("source")
        payload["pre_alarm_seconds_captured"] = pre_alarm.get("seconds_captured")
        payload["pre_alarm_segment_count"] = pre_alarm.get("segment_count")
        if pre_alarm.get("path"):
            payload["pre_alarm_path"] = pre_alarm.get("path")
    await update_recording_session(session_id, payload)


def _result(
    *,
    recording_status: str,
    recording_session_id: Optional[str] = None,
    pre_alarm: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if recording_status not in RECORDING_ACTION_STATUSES:
        recording_status = "failed"
    out: dict[str, Any] = {"recording_status": recording_status}
    if recording_session_id:
        out["recording_session_id"] = recording_session_id
    if pre_alarm is not None:
        out["pre_alarm"] = pre_alarm
    return out


async def start_alarm_triggered_recording(
    camera_id: str,
    *,
    event_id: str,
    rule_id: str,
    source_type: str,
    duration_seconds: int | None = None,
    pre_alarm_seconds: int | None = None,
    post_alarm_seconds: int | None = None,
) -> dict[str, Any]:
    """Start or extend alarm-owned recording without changing the normal schedule.

    Pre-alarm freezes real Instant Replay buffer footage when available.
    Post-alarm uses duration auto-stop (extends on repeated alarms).
    """
    cid = str(camera_id or "").strip()
    if not cid:
        return _result(recording_status="failed")

    if not is_recording_engine_enabled():
        return _result(recording_status="engine_disabled")

    if not recording_sched.master_enabled:
        return _result(recording_status="master_disabled")

    post = int(
        post_alarm_seconds
        if post_alarm_seconds is not None
        else (duration_seconds if duration_seconds is not None else POST_ALARM_DEFAULT_SECONDS)
    )
    pre = int(pre_alarm_seconds if pre_alarm_seconds is not None else PRE_ALARM_DEFAULT_SECONDS)
    post = max(1, post)
    pre = max(0, pre)
    event_time = _utcnow()
    auto_stop_at = event_time + timedelta(seconds=post)

    cam = await get_camera_by_ref(cid)
    camera_uid = _camera_uid_from_doc(cam, cid)

    async with _lock_for(cid):
        # Freeze pre-alarm ASAP (before starting new FFmpeg) into a temp holding area.
        pre_hold = Path(tempfile.mkdtemp(prefix="pre_alarm_hold_"))
        pre_info: dict[str, Any]
        try:
            if pre > 0:
                pre_info = await _freeze_pre_alarm(
                    camera_id=cid,
                    camera_uid=camera_uid,
                    pre_alarm_seconds=pre,
                    event_time=event_time,
                    dest_dir=pre_hold,
                )
            else:
                pre_info = {
                    "status": "skipped",
                    "source": None,
                    "seconds_requested": 0,
                    "seconds_captured": 0,
                    "segment_count": 0,
                }

            if await is_camera_recording(cid):
                active = await get_active_recording_session(cid)
                session_id = str((active or {}).get("id") or "")
                if not session_id:
                    return _result(recording_status="failed", pre_alarm=pre_info)

                # Attach pre-alarm snapshot under the existing session when possible.
                session_doc = active or await get_recording_session(session_id) or {"id": session_id}
                session_dir = await _session_dir_for(session_doc, cid)
                if session_dir and pre_info.get("status") in ("ok", "partial"):
                    dest = session_dir / "pre_alarm"
                    dest.mkdir(parents=True, exist_ok=True)
                    for item in pre_hold.iterdir():
                        if item.is_file():
                            shutil.copy2(item, dest / item.name)
                    pre_info = {**pre_info, "path": str(dest)}

                if is_alarm_owned_recording(cid):
                    entry = _alarm_owned[cid]
                    if entry.get("session_id") == session_id:
                        _cancel_stop_task(entry)
                        entry["auto_stop_at"] = auto_stop_at
                        entry["stop_task"] = _schedule_auto_stop(cid, session_id, auto_stop_at)
                        await _apply_alarm_session_metadata(
                            session_id,
                            event_id=event_id,
                            rule_id=rule_id,
                            source_type=source_type,
                            auto_stop_at=auto_stop_at,
                            pre_alarm_seconds=pre,
                            post_alarm_seconds=post,
                            pre_alarm=pre_info,
                        )
                        logger.info(
                            "[alarm-recording] Extended alarm session camera=%s session=%s until=%s pre=%s",
                            cid,
                            session_id,
                            _iso(auto_stop_at),
                            pre_info.get("status"),
                        )
                        return _result(
                            recording_status="extended",
                            recording_session_id=session_id,
                            pre_alarm=pre_info,
                        )

                # Permanent / non-alarm session: reuse, no duplicate FFmpeg, still link pre metadata.
                await _apply_alarm_session_metadata(
                    session_id,
                    event_id=event_id,
                    rule_id=rule_id,
                    source_type=source_type,
                    auto_stop_at=auto_stop_at,
                    pre_alarm_seconds=pre,
                    post_alarm_seconds=post,
                    pre_alarm=pre_info,
                )
                logger.info(
                    "[alarm-recording] Reusing existing non-alarm session camera=%s session=%s pre=%s",
                    cid,
                    session_id,
                    pre_info.get("status"),
                )
                return _result(
                    recording_status="already_recording",
                    recording_session_id=session_id,
                    pre_alarm=pre_info,
                )

            try:
                session = await start_camera_recording(cid)
            except RecordingEngineDisabled:
                return _result(recording_status="engine_disabled", pre_alarm=pre_info)
            except Exception as exc:
                logger.error("[alarm-recording] Start failed camera=%s: %s", cid, exc, exc_info=True)
                return _result(recording_status="failed", pre_alarm=pre_info)

            session_id = str(session.get("id") or "")
            if not session_id:
                return _result(recording_status="failed", pre_alarm=pre_info)

            session_dir = await _session_dir_for(session, cid)
            if session_dir and pre_info.get("status") in ("ok", "partial"):
                dest = session_dir / "pre_alarm"
                dest.mkdir(parents=True, exist_ok=True)
                for item in pre_hold.iterdir():
                    if item.is_file():
                        shutil.copy2(item, dest / item.name)
                pre_info = {**pre_info, "path": str(dest)}

            await _apply_alarm_session_metadata(
                session_id,
                event_id=event_id,
                rule_id=rule_id,
                source_type=source_type,
                auto_stop_at=auto_stop_at,
                pre_alarm_seconds=pre,
                post_alarm_seconds=post,
                pre_alarm=pre_info,
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
                "[alarm-recording] Started alarm session camera=%s session=%s until=%s pre=%s",
                cid,
                session_id,
                _iso(auto_stop_at),
                pre_info.get("status"),
            )
            return _result(
                recording_status="started",
                recording_session_id=session_id,
                pre_alarm=pre_info,
            )
        finally:
            shutil.rmtree(pre_hold, ignore_errors=True)


def reset_alarm_recording_for_tests() -> None:
    """Clear in-memory alarm ownership state between tests."""
    for entry in _alarm_owned.values():
        _cancel_stop_task(entry)
    _alarm_owned.clear()
    _locks.clear()
