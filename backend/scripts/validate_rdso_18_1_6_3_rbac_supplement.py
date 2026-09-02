"""Supplementary RBAC positive test for RDSO 18.1.6.3 acceptance."""
import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import alarm_rules_collection, camera_collection, events_collection, user_collection
from app.services.session_service import SESSION_COOKIE_NAME, create_session
from scripts.validate_rdso_18_1_6_3_e2e_acceptance import (
    RULE_NAME,
    _api,
    _reset_baseline,
    _run_probe_cycle,
    _cleanup_rule_and_events,
)

CAMERA_IP = os.getenv("CCTV_TEST_CAMERA_IP", "192.168.41.106")
API_BASE = os.getenv("CCTV_API_BASE", "http://127.0.0.1:10000")
GROUP = "rml_6_corporate_office_2nd_floor"


async def main() -> int:
    camera = await camera_collection.find_one({"ip_address": CAMERA_IP})
    ispit = await user_collection.find_one({"name": "ispit"})
    if not camera or not ispit:
        print("SKIP: missing camera or ispit user")
        return 0

    original_access = ispit.get("cameraAccess")
    rule_id = None
    event_ids: list[str] = []
    admin_token = await create_session(str((await user_collection.find_one({"name": "admin123"}))["_id"]))

    try:
        await user_collection.update_one(
            {"_id": ispit["_id"]},
            {"$set": {"cameraAccess.allowedCameraGroups": [GROUP]}},
        )
        await _reset_baseline(camera)

        payload = {
            "name": RULE_NAME + " RBAC",
            "enabled": True,
            "camera_id": str(camera["_id"]),
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification"],
            "severity": "warning",
            "cooldown_seconds": 60,
        }
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: admin_token}) as http:
            status, created = await _api(http, "POST", "/api/alarm-rules", json=payload)
        rule_id = created["id"]

        cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
        for _ in range(3):
            cam_doc = await camera_collection.find_one({"_id": camera["_id"]})
            result = await _run_probe_cycle(cam_doc, offline=True)
        if not result.get("alarm"):
            print("FAIL: could not trigger alarm for RBAC supplement")
            return 1

        rule_doc = await alarm_rules_collection.find_one({"_id": __import__("bson").ObjectId(rule_id)})
        event_id = str((rule_doc.get("runtime") or {}).get("last_event_id"))
        event_ids.append(event_id)

        ispit_token = await create_session(str(ispit["_id"]))
        async with aiohttp.ClientSession(cookies={SESSION_COOKIE_NAME: ispit_token}) as http:
            st, ev = await _api(http, "GET", "/api/events?limit=50")
            st2, nf = await _api(http, "GET", "/api/events?ui_notification=true&limit=50")
            st3, ack = await _api(http, "POST", f"/api/events/{event_id}/acknowledge")

        ev_ids = [e.get("id") for e in (ev or {}).get("items", [])]
        nf_ids = [e.get("id") for e in (nf or {}).get("items", [])]
        if event_id not in ev_ids or event_id not in nf_ids:
            print(f"FAIL: operator with ACL cannot see event ev={event_id in ev_ids} nf={event_id in nf_ids}")
            return 1
        if st3 != 200 or not ack.get("acknowledged"):
            print(f"FAIL: operator acknowledge failed status={st3}")
            return 1
        print(f"PASS: ispit with temp ACL saw event {event_id} on Events+Notifications and acknowledged")
        return 0
    finally:
        await _cleanup_rule_and_events(rule_id, event_ids)
        await _reset_baseline(camera)
        if original_access is None:
            await user_collection.update_one({"_id": ispit["_id"]}, {"$unset": {"cameraAccess": ""}})
        else:
            await user_collection.update_one({"_id": ispit["_id"]}, {"$set": {"cameraAccess": original_access}})


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
