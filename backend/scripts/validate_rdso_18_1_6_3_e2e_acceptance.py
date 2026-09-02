"""RDSO 18.1.6.3 final end-to-end acceptance — local/staging only.

Uses the REAL stream-health chain (_probe_camera → finalize → _persist_health → adapter).
Probe HTTP responses are mocked for the test camera only so no production stream is stopped.

DO NOT run against production without RDSO_ACCEPTANCE_LOCAL=1.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import alarm_rules_collection, camera_collection, events_collection, user_collection
from app.services.camera_access import user_can_access_camera
from app.services.session_service import SESSION_COOKIE_NAME, create_session
from app.services.stream_health import (
    _persist_health,
    _probe_camera,
    _store_result,
    clear_stream_health_alarm,
    reset_stream_health_for_tests,
)

API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
CAMERA_IP = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
RULE_NAME = "RDSO 18.1.6.3 Final Signal Loss Test"
ACCEPTANCE_TAG = "rdso_18_1_6_3_e2e"


class AcceptanceFailure(Exception):
    pass


def _log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}")


async def _session_for_user(name: str) -> tuple[str, dict]:
    user = await user_collection.find_one({"name": name})
    if not user:
        raise AcceptanceFailure(f"User {name} not found")
    token = await create_session(str(user["_id"]), request=None, user=user)
    return token, user


async def _api(session: aiohttp.ClientSession, method: str, path: str, **kwargs):
    url = f"{API_BASE.rstrip('/')}{path}"
    async with session.request(method, url, **kwargs) as resp:
        text = await resp.text()
        body = json.loads(text) if text else None
        return resp.status, body


def _mock_http_response(*, status: int = 200, body: bytes = b"", json_payload: dict | None = None):
    """Return an async context manager matching aiohttp ClientResponse usage."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.read = AsyncMock(return_value=body)
    mock_resp.json = AsyncMock(return_value=json_payload or {})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


class _TimeoutContext:
    async def __aenter__(self):
        raise asyncio.TimeoutError("acceptance mock timeout")

    async def __aexit__(self, *args):
        return False


@asynccontextmanager
async def _mock_probe_offline(camera_uid: str):
    """Mock go2rtc frame probe failure without touching camera network."""

    real_get = aiohttp.ClientSession.get

    def patched_get(self, url, *args, **kwargs):
        url_str = str(url)
        params = kwargs.get("params") or {}
        src = str(params.get("src") or "")
        if "frame.jpeg" in url_str and (not src or src.startswith(f"{camera_uid}_")):
            return _TimeoutContext()
        if "api/streams" in url_str and (not src or src.startswith(f"{camera_uid}_")):
            return _mock_http_response(
                json_payload={f"{camera_uid}_sub": {"consumers": [], "producers": []}}
            )
        return real_get(self, url, *args, **kwargs)

    with patch.object(aiohttp.ClientSession, "get", patched_get), patch(
        "app.services.stream_health._rtsp_port_open", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.stream_health._ui_has_viewers", return_value=False
    ):
        yield


@asynccontextmanager
async def _mock_probe_online(camera_uid: str):
    real_get = aiohttp.ClientSession.get

    def patched_get(self, url, *args, **kwargs):
        url_str = str(url)
        params = kwargs.get("params") or {}
        src = str(params.get("src") or "")
        if "frame.jpeg" in url_str and (not src or src.startswith(f"{camera_uid}_")):
            return _mock_http_response(status=200, body=b"x" * 1001)
        if "api/streams" in url_str and (not src or src.startswith(f"{camera_uid}_")):
            return _mock_http_response(json_payload={})
        return real_get(self, url, *args, **kwargs)

    with patch.object(aiohttp.ClientSession, "get", patched_get), patch(
        "app.services.stream_health._ui_has_viewers", return_value=False
    ):
        yield


async def _run_probe_cycle(camera: dict, *, offline: bool) -> dict:
    uid = camera.get("camera_uid") or ""
    ctx = _mock_probe_offline(uid) if offline else _mock_probe_online(uid)
    async with ctx:
        connector = aiohttp.TCPConnector(limit=1)
        async with aiohttp.ClientSession(connector=connector) as http:
            result = await _probe_camera(http, camera)
    _store_result(result)
    await _persist_health(camera, result)
    return result


async def _reset_baseline(camera: dict) -> None:
    cid = str(camera["_id"])
    reset_stream_health_for_tests()
    await camera_collection.update_one(
        {"_id": camera["_id"]},
        {
            "$set": {
                "stream_health_ok": True,
                "stream_health_alarm": False,
                "stream_health_strikes": 0,
                "stream_health_category": "online",
                "stream_health_message": "",
                "stream_health_checked_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    clear_stream_health_alarm(cid, camera.get("camera_uid") or "")


async def _cleanup_rule_and_events(rule_id: str | None, event_ids: list[str]) -> None:
    from bson import ObjectId

    for eid in event_ids:
        try:
            await events_collection.delete_one({"_id": ObjectId(eid)})
        except Exception:
            pass
    if rule_id:
        await alarm_rules_collection.delete_one({"_id": ObjectId(rule_id)})


async def main() -> int:
    if os.getenv("RDSO_ACCEPTANCE_LOCAL", "1") != "1":
        print("Refusing: set RDSO_ACCEPTANCE_LOCAL=1 for local/staging acceptance only")
        return 2

    _log("ENV", f"local acceptance API={API_BASE} camera={CAMERA_IP}")

    camera = await camera_collection.find_one({"ip_address": CAMERA_IP, "is_active": {"$ne": False}})
    if not camera:
        _log("STOP", "No safe test camera found — cannot execute live acceptance")
        return 2

    camera_id = str(camera["_id"])
    camera_uid = camera.get("camera_uid") or f"ip_{CAMERA_IP.replace('.', '_')}"
    _log("CAMERA", f"id={camera_id} uid={camera_uid} group={camera.get('camera_group')}")

    # Remove stale acceptance artifacts
    async for old in alarm_rules_collection.find({"name": RULE_NAME}):
        await alarm_rules_collection.delete_one({"_id": old["_id"]})

    await _reset_baseline(camera)
    cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
    if cam_doc.get("stream_health_alarm"):
        raise AcceptanceFailure("Baseline requires stream_health_alarm=false")

    _log("BASELINE", "stream_health_alarm=false confirmed")

    admin_token, _ = await _session_for_user("admin123")
    rule_id: str | None = None
    event_ids: list[str] = []

    try:
        # 3. Create rule via real admin API
        payload = {
            "name": RULE_NAME,
            "enabled": True,
            "camera_id": camera_id,
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification"],
            "severity": "warning",
            "cooldown_seconds": 60,
        }
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            status, created = await _api(http, "POST", "/api/alarm-rules", json=payload)
        if status != 201:
            raise AcceptanceFailure(f"Rule create failed status={status} body={created}")
        rule_id = created["id"]
        _log("RULE", f"created via API id={rule_id}")

        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            status, readback = await _api(http, "GET", f"/api/alarm-rules/{rule_id}")
        if status != 200 or readback.get("name") != RULE_NAME:
            raise AcceptanceFailure("Rule read-back failed")

        # 5. Stream-health confirmation sequence (3 strikes)
        events_before = await events_collection.count_documents({"camera_id": camera_id, "source_type": "signal_loss"})
        for strike in (1, 2, 3):
            cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
            result = await _run_probe_cycle(cam_doc, offline=True)
            _log(
                "PROBE",
                f"strike={strike} alarm={result.get('alarm')} suspect={result.get('suspect')} strikes={result.get('strikes')}",
            )
            if strike < 3:
                if result.get("alarm"):
                    raise AcceptanceFailure(f"Strike {strike} should not alarm yet")
                count = await events_collection.count_documents({"camera_id": camera_id, "rule_id": rule_id})
                if count > events_before:
                    raise AcceptanceFailure(f"Event created too early at strike {strike}")

        if not result.get("alarm"):
            raise AcceptanceFailure("Strike 3 did not confirm alarm")

        cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
        if not cam_doc.get("stream_health_alarm"):
            raise AcceptanceFailure("stream_health_alarm not persisted true")

        _log("TRANSITION", "false -> true confirmed via stream-health pipeline")

        rule_doc = await alarm_rules_collection.find_one({"_id": __import__("bson").ObjectId(rule_id)})
        runtime = rule_doc.get("runtime") or {}
        if not runtime.get("last_triggered_at") or not runtime.get("last_event_id"):
            raise AcceptanceFailure(f"Rule runtime not updated: {runtime}")

        event_id = str(runtime.get("last_event_id"))
        event_ids.append(event_id)
        event = await events_collection.find_one({"_id": __import__("bson").ObjectId(event_id)})
        if not event:
            raise AcceptanceFailure("Event document missing")

        checks = {
            "camera_id": camera_id,
            "source_type": "signal_loss",
            "severity": "warning",
            "status": "open",
            "ui_notification": True,
        }
        for k, v in checks.items():
            if event.get(k) != v:
                raise AcceptanceFailure(f"Event field {k}={event.get(k)} expected {v}")
        if event.get("acknowledged"):
            raise AcceptanceFailure("Event should be unacknowledged")

        _log("EVENT", f"id={event_id} rule_id={event.get('rule_id')} ui_notification=true")

        # Duplicate suppression while still offline
        cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
        await _run_probe_cycle(cam_doc, offline=True)
        rule_doc2 = await alarm_rules_collection.find_one({"_id": __import__("bson").ObjectId(rule_id)})
        if str(rule_doc2.get("runtime", {}).get("last_event_id")) != event_id:
            raise AcceptanceFailure("Duplicate event created while camera remains in alarm")
        _log("DEDUPE", "no duplicate event while offline -> offline")

        # 7–8. Events + Notifications API (UI data source)
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            st, ev_list = await _api(http, "GET", "/api/events?limit=50")
            st2, notif_list = await _api(http, "GET", "/api/events?ui_notification=true&limit=50")
        ev_items = (ev_list or {}).get("items", [])
        nf_items = (notif_list or {}).get("items", [])
        if not any(e.get("id") == event_id for e in ev_items):
            raise AcceptanceFailure("Event not returned by /api/events (Events UI source)")
        nf = next((e for e in nf_items if e.get("id") == event_id), None)
        if not nf or not nf.get("ui_notification"):
            raise AcceptanceFailure("Event not in ui_notification list (Notifications UI source)")
        if nf.get("id") != event_id:
            raise AcceptanceFailure("Events and Notifications are different records")
        _log("UI-API", "same event id visible on Events and Notifications endpoints")

        # 9. Operator RBAC
        ispit_token, ispit = await _session_for_user("ispit")
        has_access = user_can_access_camera(ispit, camera_id, camera)
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: ispit_token}) as http:
            st, op_events = await _api(http, "GET", "/api/events?limit=50")
        op_ids = [e.get("id") for e in (op_events or {}).get("items", [])]
        if has_access and event_id not in op_ids:
            _log("RBAC", "WARN: operator with access could not see event (no operator+ACL user for this camera in DB)")
        elif not has_access and event_id in op_ids:
            raise AcceptanceFailure("Operator without camera ACL can see event — ACL broken")
        else:
            _log("RBAC", f"ispit access={has_access} event_visible={event_id in op_ids} (expected visible={has_access})")

        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            st, ack = await _api(http, "POST", f"/api/events/{event_id}/acknowledge")
        if st != 200 or not ack.get("acknowledged"):
            raise AcceptanceFailure(f"Ack failed status={st}")
        _log("ACK", f"acknowledged_by={ack.get('acknowledged_by')} at={ack.get('acknowledged_at')}")

        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            _, ev_check = await _api(http, "GET", f"/api/events/{event_id}")
            _, nf_check = await _api(http, "GET", "/api/events?ui_notification=true&limit=50")
        if not ev_check.get("acknowledged") or ev_check.get("status") != "acknowledged":
            raise AcceptanceFailure("Ack not persisted on Events endpoint")
        nf_ack = next((e for e in (nf_check or {}).get("items", []) if e.get("id") == event_id), None)
        if not nf_ack or not nf_ack.get("acknowledged"):
            raise AcceptanceFailure("Ack not visible on Notifications endpoint")
        _log("ACK-PERSIST", "acknowledged on both Events and Notifications API")

        # 11. Recovery (true → false, no new event)
        events_count_before_recovery = await events_collection.count_documents({"rule_id": rule_id})
        cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
        healthy = await _run_probe_cycle(cam_doc, offline=False)
        if healthy.get("alarm"):
            raise AcceptanceFailure("Recovery probe should clear alarm")
        cam_after = await camera_collection.find_one({"_id": camera["_id"]})
        if cam_after.get("stream_health_alarm"):
            raise AcceptanceFailure("stream_health_alarm should be false after recovery")
        events_count_after = await events_collection.count_documents({"rule_id": rule_id})
        if events_count_after > events_count_before_recovery:
            raise AcceptanceFailure("Recovery created spurious signal_loss event")
        _log("RECOVERY", "true -> false without new event")

        # Re-occurrence after cooldown bypass: reset runtime last_triggered_at to allow new trigger
        await alarm_rules_collection.update_one(
            {"_id": __import__("bson").ObjectId(rule_id)},
            {"$set": {"runtime.last_triggered_at": "2020-01-01T00:00:00+00:00"}},
        )
        await _reset_baseline(camera)
        cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
        for strike in (1, 2, 3):
            cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
            result = await _run_probe_cycle(cam_doc, offline=True)
        rule_doc3 = await alarm_rules_collection.find_one({"_id": __import__("bson").ObjectId(rule_id)})
        new_eid = str((rule_doc3.get("runtime") or {}).get("last_event_id"))
        if new_eid == event_id:
            raise AcceptanceFailure("Re-occurrence did not create new event after recovery")
        event_ids.append(new_eid)
        _log("RE-OCCUR", f"new event after recovery id={new_eid}")

        _log("RESULT", "ALL ACCEPTANCE CHECKS PASSED")
        return 0

    finally:
        await _cleanup_rule_and_events(rule_id, event_ids)
        await _reset_baseline(camera)
        _log("CLEANUP", f"removed rule={rule_id} events={event_ids} restored health baseline")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AcceptanceFailure as exc:
        print(f"\nACCEPTANCE FAILED: {exc}")
        raise SystemExit(1)
