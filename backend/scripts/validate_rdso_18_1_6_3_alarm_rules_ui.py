"""Manual validation for RDSO 18.1.6.3 Step 4 — alarm rule CRUD via live HTTP API.

Uses a programmatic admin session (no password needed). Does not take cameras offline.
Run: python scripts/validate_rdso_18_1_6_3_alarm_rules_ui.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import aiohttp
from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import alarm_rules_collection, camera_collection, user_collection
from app.services.session_service import SESSION_COOKIE_NAME, create_session

API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
RULE_NAME = "RDSO Signal Loss Test"


async def _pick_admin() -> dict:
    for query in (
        {"role": {"$regex": "^admin$", "$options": "i"}},
        {"role": {"$regex": "super_admin", "$options": "i"}},
    ):
        doc = await user_collection.find_one(query)
        if doc:
            return doc
    raise RuntimeError("No Admin/SUPER_ADMIN user found in MongoDB")


async def _pick_safe_camera() -> dict:
    preferred_ip = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
    cam = await camera_collection.find_one({"ip_address": preferred_ip, "is_active": {"$ne": False}})
    if not cam:
        cam = await camera_collection.find_one({"is_active": {"$ne": False}})
    if not cam:
        raise RuntimeError("No active camera found for test rule")
    return cam


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
) -> tuple[int, Any]:
    url = f"{API_BASE.rstrip('/')}{path}"
    async with session.request(method, url, json=json_body) as resp:
        text = await resp.text()
        if not text:
            return resp.status, None
        try:
            return resp.status, json.loads(text)
        except json.JSONDecodeError:
            return resp.status, text


async def main() -> int:
    admin = await _pick_admin()
    camera = await _pick_safe_camera()
    camera_id = str(camera["_id"])
    cam_label = camera.get("display_name") or camera.get("name") or camera.get("ip_address") or camera_id

    token = await create_session(
        str(admin["_id"]),
        request=None,
        user={
            "_id": admin["_id"],
            "name": admin.get("name"),
            "username": admin.get("username") or admin.get("name"),
            "role": admin.get("role"),
        },
    )

    print(f"API base: {API_BASE}")
    print(f"Admin session: {admin.get('name')} ({admin.get('role')})")
    print(f"Test camera: {cam_label} ({camera.get('ip_address')}) id={camera_id}")

    rule_id: str | None = None
    cookie = {SESSION_COOKIE_NAME: token}

    async with aiohttp.ClientSession(cookies=cookie) as http:
        # Cleanup any prior test rule with same name
        status, listed = await _request(http, "GET", "/api/alarm-rules?limit=200")
        if status == 200 and isinstance(listed, dict):
            for item in listed.get("items") or []:
                if item.get("name") == RULE_NAME:
                    rid = item.get("id")
                    if rid:
                        await _request(http, "DELETE", f"/api/alarm-rules/{rid}")
                        print(f"Removed stale test rule {rid}")

        payload = {
            "name": RULE_NAME,
            "enabled": True,
            "camera_id": camera_id,
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification"],
            "severity": "warning",
            "cooldown_seconds": 60,
        }

        print("\n[1] CREATE rule")
        status, created = await _request(http, "POST", "/api/alarm-rules", json_body=payload)
        if status != 201 or not isinstance(created, dict):
            print(f"FAIL create status={status} body={created}")
            return 1
        rule_id = created.get("id")
        print(f"OK created id={rule_id} cooldown={created.get('cooldown_seconds')}")

        print("\n[2] LIST / refresh persistence")
        status, listed = await _request(http, "GET", "/api/alarm-rules?limit=200")
        found = next((r for r in (listed or {}).get("items", []) if r.get("id") == rule_id), None)
        if status != 200 or not found:
            print(f"FAIL list after create status={status}")
            return 1
        print(f"OK rule visible after list name={found.get('name')}")

        print("\n[3] EDIT cooldown 60 -> 120")
        status, updated = await _request(
            http,
            "PUT",
            f"/api/alarm-rules/{rule_id}",
            json_body={"cooldown_seconds": 120},
        )
        if status != 200 or (updated or {}).get("cooldown_seconds") != 120:
            print(f"FAIL update cooldown status={status} body={updated}")
            return 1
        print("OK cooldown=120")

        status, got = await _request(http, "GET", f"/api/alarm-rules/{rule_id}")
        if status != 200 or (got or {}).get("cooldown_seconds") != 120:
            print(f"FAIL get after cooldown edit status={status}")
            return 1
        print("OK persisted after refresh (GET by id)")

        print("\n[4] DISABLE rule")
        status, disabled = await _request(
            http,
            "PUT",
            f"/api/alarm-rules/{rule_id}",
            json_body={"enabled": False},
        )
        if status != 200 or disabled.get("enabled") is not False:
            print(f"FAIL disable status={status} body={disabled}")
            return 1
        status, got = await _request(http, "GET", f"/api/alarm-rules/{rule_id}")
        if (got or {}).get("enabled") is not False:
            print("FAIL disabled state not persisted")
            return 1
        print("OK enabled=false persisted")

        print("\n[5] RE-ENABLE then DELETE cleanup")
        await _request(http, "PUT", f"/api/alarm-rules/{rule_id}", json_body={"enabled": True})
        status, _ = await _request(http, "DELETE", f"/api/alarm-rules/{rule_id}")
        if status != 204:
            print(f"FAIL delete status={status}")
            return 1
        status, got = await _request(http, "GET", f"/api/alarm-rules/{rule_id}")
        if status != 404:
            print(f"FAIL rule still exists after delete status={status}")
            return 1
        print("OK rule deleted and gone from API")

    doc = await alarm_rules_collection.find_one({"name": RULE_NAME})
    if doc:
        print("WARN test rule document still in Mongo — manual cleanup may be needed")
        return 1

    print("\nALL STEPS PASSED — RDSO 18.1.6.3 Step 4 manual API validation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
