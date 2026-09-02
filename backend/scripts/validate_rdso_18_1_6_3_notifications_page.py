"""Manual validation for RDSO 18.1.6.3 Step 6 — Notifications share events system-of-record."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import camera_collection, events_collection, user_collection
from app.services.alarm_rule_evaluator import process_test_alarm_signal
from app.services.alarm_rule_service import create_alarm_rule, delete_alarm_rule
from app.services.session_service import SESSION_COOKIE_NAME, create_session

API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
CAMERA_IP = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
RULE_NAME = "RDSO Step6 Notifications Test"


async def _admin_session() -> str:
    admin = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not admin:
        admin = await user_collection.find_one({"role": {"$regex": "super_admin", "$options": "i"}})
    return await create_session(str(admin["_id"]), request=None, user=admin)


async def _get(session, path):
    async with session.get(f"{API_BASE}{path}") as resp:
        return resp.status, json.loads(await resp.text()) if resp.content_length != 0 else None


async def _post(session, path):
    async with session.post(f"{API_BASE}{path}") as resp:
        return resp.status, json.loads(await resp.text()) if resp.content_length != 0 else None


async def main() -> int:
    camera = await camera_collection.find_one({"ip_address": CAMERA_IP, "is_active": {"$ne": False}})
    if not camera:
        print("FAIL: camera not found")
        return 1
    camera_id = str(camera["_id"])
    uid = camera.get("camera_uid") or f"ip_{CAMERA_IP.replace('.', '_')}"

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
        created_by="validate_step6",
    )
    rule_id = rule["id"]

    result = await process_test_alarm_signal(
        {
            "camera_id": camera_id,
            "camera_uid": uid,
            "source_type": "signal_loss",
            "occurred_at": "2026-09-01T15:00:00+00:00",
            "title": "Camera signal lost",
            "message": "Step 6 notifications validation",
            "metadata": {"health_category": "timeout", "strikes": 3},
        }
    )
    event_id = (result.get("events_created") or [None])[0]
    if not event_id:
        print(f"FAIL: no event {result}")
        return 1

    token = await _admin_session()
    async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: token}) as http:
        print("[1] Events list contains event")
        status, events = await _get(http, "/api/events?limit=50")
        if not any(e.get("id") == event_id for e in (events or {}).get("items", [])):
            print("FAIL: not in events list")
            return 1

        print("[2] Notifications filter ui_notification=true")
        status, notifs = await _get(http, "/api/events?ui_notification=true&limit=50")
        found = next((e for e in (notifs or {}).get("items", []) if e.get("id") == event_id), None)
        if not found or not found.get("ui_notification"):
            print("FAIL: not in ui notifications")
            return 1
        if found.get("source_type") != "signal_loss":
            print("FAIL: wrong source")
            return 1

        print("[3] ui_notification=false excludes it")
        status, non = await _get(http, "/api/events?ui_notification=false&limit=50")
        if any(e.get("id") == event_id for e in (non or {}).get("items", [])):
            print("FAIL: appeared in false filter")
            return 1

        print("[4] Acknowledge via API")
        status, ack = await _post(http, f"/api/events/{event_id}/acknowledge")
        if status != 200 or not ack.get("acknowledged"):
            print(f"FAIL ack status={status}")
            return 1

        print("[5] Same acknowledged state on Events GET")
        status, ev = await _get(http, f"/api/events/{event_id}")
        if not ev.get("acknowledged"):
            print("FAIL: events page would show unacknowledged")
            return 1

    from bson import ObjectId

    await events_collection.delete_one({"_id": ObjectId(event_id)})
    await delete_alarm_rule(rule_id)
    print("\nALL STEPS PASSED — Step 6 Notifications validation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
