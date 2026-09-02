"""RDSO 18.1.6.5 acceptance — alarm-triggered recording on local/staging only."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Must be set before recording modules evaluate env gates.
os.environ.setdefault("RECORDING_ENABLED", "true")

from app.core.database import alarm_rules_collection, camera_collection, events_collection, recording_sessions_collection
from app.services.alarm_recording_service import is_alarm_owned_recording, reset_alarm_recording_for_tests
from app.services.recording_schedule_store import load_recording_settings, recording_schedule, save_recording_settings
from app.services.session_service import SESSION_COOKIE_NAME, create_session
from app.services.video_recording import ACTIVE_RECORDINGS, is_camera_recording, stop_camera_recording
from scripts.validate_rdso_18_1_6_3_e2e_acceptance import (
    AcceptanceFailure,
    _api,
    _cleanup_rule_and_events,
    _log,
    _reset_baseline,
    _run_probe_cycle,
    _session_for_user,
)

API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
CAMERA_IP = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
RULE_NAME = "RDSO 18.1.6.5 Recording Test"
DURATION_SECONDS = int(os.getenv("RDSO_RECORDING_TEST_SECONDS", "25"))


async def _set_master(enabled: bool) -> None:
    from app.services import recording_schedule_store as sched

    sched.master_enabled = enabled
    await sched.save_recording_settings()


async def _ensure_schedule_off(camera_id: str) -> bool:
    from app.services import recording_schedule_store as sched

    await load_recording_settings()
    before = bool(sched.recording_schedule.get(camera_id, False))
    sched.recording_schedule[camera_id] = False
    await save_recording_settings()
    return before


async def _restore_schedule(camera_id: str, previous: bool) -> None:
    from app.services import recording_schedule_store as sched

    sched.recording_schedule[camera_id] = previous
    await save_recording_settings()


async def main() -> int:
    if os.getenv("RDSO_ACCEPTANCE_LOCAL", "1") != "1":
        print("Refusing: set RDSO_ACCEPTANCE_LOCAL=1 for local/staging acceptance only")
        return 2

    from app.services.recording_config import is_recording_engine_enabled

    if not is_recording_engine_enabled():
        print("Refusing: RECORDING_ENABLED must be true for 18.1.6.5 acceptance")
        return 2

    reset_alarm_recording_for_tests()
    _log("ENV", f"local acceptance API={API_BASE} camera={CAMERA_IP} duration={DURATION_SECONDS}s")

    camera = await camera_collection.find_one({"ip_address": CAMERA_IP, "is_active": {"$ne": False}})
    if not camera:
        _log("STOP", "No safe test camera found")
        return 2

    camera_id = str(camera["_id"])
    schedule_before = await _ensure_schedule_off(camera_id)
    master_before = False
    await load_recording_settings()
    from app.services import recording_schedule_store as sched

    master_before = bool(sched.master_enabled)
    await _set_master(True)

    admin_token, _ = await _session_for_user("admin123")
    rule_id: str | None = None
    event_ids: list[str] = []
    session_id: str | None = None

    try:
        async for old in alarm_rules_collection.find({"name": RULE_NAME}):
            await alarm_rules_collection.delete_one({"_id": old["_id"]})

        await _reset_baseline(camera)

        payload = {
            "name": RULE_NAME,
            "enabled": True,
            "camera_id": camera_id,
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification", "start_recording"],
            "severity": "warning",
            "cooldown_seconds": 60,
            "recording": {"duration_seconds": DURATION_SECONDS},
        }
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            status, created = await _api(http, "POST", "/api/alarm-rules", json=payload)
        if status != 201:
            raise AcceptanceFailure(f"Rule create failed status={status} body={created}")
        rule_id = created["id"]
        _log("RULE", f"created via API id={rule_id}")

        for strike in (1, 2, 3):
            cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
            result = await _run_probe_cycle(cam_doc, offline=True)
            _log("PROBE", f"strike={strike} alarm={result.get('alarm')}")

        if not await is_camera_recording(camera_id):
            raise AcceptanceFailure("Alarm did not start recording")

        if len(ACTIVE_RECORDINGS) != 1:
            raise AcceptanceFailure(f"Expected one ACTIVE_RECORDINGS entry, got {len(ACTIVE_RECORDINGS)}")

        if not is_alarm_owned_recording(camera_id):
            raise AcceptanceFailure("Recording is not alarm-owned")

        await load_recording_settings()
        if recording_schedule.get(camera_id):
            raise AcceptanceFailure("Schedule flag must remain false during alarm recording")

        rule_doc = await alarm_rules_collection.find_one({"_id": __import__("bson").ObjectId(rule_id)})
        event_id = str((rule_doc.get("runtime") or {}).get("last_event_id"))
        event_ids.append(event_id)
        event = await events_collection.find_one({"_id": __import__("bson").ObjectId(event_id)})
        if event.get("recording_status") != "started":
            raise AcceptanceFailure(f"Event recording_status={event.get('recording_status')}")
        session_id = str(event.get("recording_session_id") or "")
        if not session_id:
            raise AcceptanceFailure("Event missing recording_session_id")

        session = await recording_sessions_collection.find_one({"_id": __import__("bson").ObjectId(session_id)})
        if session.get("start_reason") != "alarm" or session.get("event_id") != event_id:
            raise AcceptanceFailure("Session missing alarm linkage metadata")

        _log("RECORDING", f"started session={session_id} schedule=false alarm_owned=true")

        _log("WAIT", f"waiting {DURATION_SECONDS + 5}s for auto-stop")
        await asyncio.sleep(DURATION_SECONDS + 5)

        if await is_camera_recording(camera_id):
            raise AcceptanceFailure("Alarm recording did not auto-stop")

        await load_recording_settings()
        if recording_schedule.get(camera_id):
            raise AcceptanceFailure("Schedule flag changed after auto-stop")

        stopped = await recording_sessions_collection.find_one({"_id": __import__("bson").ObjectId(session_id)})
        if stopped.get("status") != "stopped":
            raise AcceptanceFailure(f"Session status={stopped.get('status')} expected stopped")

        _log("AUTO-STOP", "alarm-owned recording stopped; schedule still false")
        _log("RESULT", "ALL ACCEPTANCE CHECKS PASSED")
        return 0

    finally:
        if await is_camera_recording(camera_id):
            try:
                await stop_camera_recording(camera_id)
            except Exception:
                pass
        reset_alarm_recording_for_tests()
        await _cleanup_rule_and_events(rule_id, event_ids)
        await _reset_baseline(camera)
        await _restore_schedule(camera_id, schedule_before)
        await _set_master(master_before)
        _log("CLEANUP", f"rule={rule_id} events={event_ids} session={session_id}")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AcceptanceFailure as exc:
        print(f"\nACCEPTANCE FAILED: {exc}")
        raise SystemExit(1)
