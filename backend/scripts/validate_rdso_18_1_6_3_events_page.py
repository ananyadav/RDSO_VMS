"""Manual validation for RDSO 18.1.6.3 Step 5 — real Events page data path.

Creates a temporary rule, injects one synthetic signal_loss event via the internal
evaluator test helper, verifies list/get/ack API, then cleans up.

Does NOT disconnect cameras or alter RTSP/network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import alarm_rules_collection, camera_collection, events_collection, user_collection
from app.services.alarm_rule_evaluator import process_test_alarm_signal
from app.services.session_service import SESSION_COOKIE_NAME, create_session

API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
CAMERA_IP = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
RULE_NAME = "RDSO Step5 Events Test Rule"


async def _admin_session() -> str:
    admin = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not admin:
        admin = await user_collection.find_one({"role": {"$regex": "super_admin", "$options": "i"}})
    if not admin:
        raise RuntimeError("No admin user")
    return await create_session(str(admin["_id"]), request=None, user=admin)


async def _request(session: aiohttp.ClientSession, method: str, path: str, **kwargs):
    url = f"{API_BASE.rstrip('/')}{path}"
    async with session.request(method, url, **kwargs) as resp:
        text = await resp.text()
        body = json.loads(text) if text else None
        return resp.status, body


async def main() -> int:
    camera = await camera_collection.find_one({"ip_address": CAMERA_IP, "is_active": {"$ne": False}})
    if not camera:
        camera = await camera_collection.find_one({"is_active": {"$ne": False}})
    if not camera:
        print("FAIL: no camera")
        return 1

    camera_id = str(camera["_id"])
    uid = camera.get("camera_uid") or f"ip_{CAMERA_IP.replace('.', '_')}"
    token = await _admin_session()
    rule_id = None
    event_id = None

    print(f"Camera: {CAMERA_IP} id={camera_id}")

    # Ensure enabled signal_loss rule exists briefly
    from app.services.alarm_rule_service import create_alarm_rule, delete_alarm_rule

    rule = await create_alarm_rule(
        {
            "name": RULE_NAME,
            "enabled": True,
            "camera_id": camera_id,
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification"],
            "severity": "warning",
            "cooldown_seconds": 60,
        },
        created_by="validate_step5",
    )
    rule_id = rule["id"]
    print(f"Created temp rule {rule_id}")

    signal = {
        "camera_id": camera_id,
        "camera_uid": uid,
        "source_type": "signal_loss",
        "occurred_at": "2026-09-01T14:00:00+00:00",
        "title": "Camera signal lost",
        "message": "Step 5 validation synthetic signal",
        "metadata": {"health_category": "timeout", "strikes": 3},
    }
    result = await process_test_alarm_signal(signal)
    event_ids = result.get("events_created") or []
    if not event_ids:
        print(f"FAIL: no event created result={result}")
        return 1
    event_id = str(event_ids[0])
    print(f"Created event {event_id}")

    async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: token}) as http:
        print("\n[1] LIST events")
        status, listed = await _request(http, "GET", "/api/events?limit=50")
        if status != 200:
            print(f"FAIL list status={status}")
            return 1
        found = next((e for e in (listed or {}).get("items", []) if e.get("id") == event_id), None)
        if not found:
            print("FAIL event not in list")
            return 1
        assert found.get("source_type") == "signal_loss"
        assert found.get("severity") == "warning"
        print("OK event visible in list")

        print("\n[2] GET event by id")
        status, got = await _request(http, "GET", f"/api/events/{event_id}")
        if status != 200 or got.get("camera_id") != camera_id:
            print(f"FAIL get status={status}")
            return 1
        print("OK GET by id")

        print("\n[3] ACKNOWLEDGE")
        status, ack = await _request(http, "POST", f"/api/events/{event_id}/acknowledge")
        if status != 200 or not ack.get("acknowledged"):
            print(f"FAIL ack status={status} body={ack}")
            return 1
        status, got = await _request(http, "GET", f"/api/events/{event_id}")
        if not got.get("acknowledged"):
            print("FAIL acknowledged state not persisted")
            return 1
        print("OK acknowledged persists")

    # Cleanup
    if event_id:
        from bson import ObjectId

        await events_collection.delete_one({"_id": ObjectId(event_id)})
    if rule_id:
        await delete_alarm_rule(rule_id)

    leftover = await alarm_rules_collection.find_one({"name": RULE_NAME})
    if leftover:
        print("WARN rule cleanup incomplete")
        return 1

    print("\nALL STEPS PASSED — Step 5 Events API validation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
