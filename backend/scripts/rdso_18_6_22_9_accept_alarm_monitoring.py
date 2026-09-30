#!/usr/bin/env python3
"""RDSO 18.6.22.9 — opt-in acceptance (synthetic alarm injection only).

Enable with:
  RDSO_18_6_22_9_ACCEPTANCE=1 python backend/scripts/rdso_18_6_22_9_accept_alarm_monitoring.py

Optional:
  ACCEPT_BASE=http://127.0.0.1:10000

Does NOT disconnect production cameras or open fleet recording.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _enabled() -> bool:
    return os.getenv("RDSO_18_6_22_9_ACCEPTANCE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


async def run() -> int:
    if not _enabled():
        print("SKIP: set RDSO_18_6_22_9_ACCEPTANCE=1 to run")
        return 0

    from bson import ObjectId

    from app.services.ccc_alarm_monitoring_service import (
        STATE_ALARM,
        STATE_MONITORING,
        list_alarm_monitoring,
        recover_alarm_monitoring,
        resolve_affected_zone,
        save_zone_camera_map,
    )
    from app.services.ccc_vms_source import ccc_capability_public
    from app.services.client_media_routing import frame_jpeg_path
    from app.services.event_service import create_event
    from app.core.database import camera_collection, events_collection

    cap = ccc_capability_public()
    assert cap["clauses"]["18.6.22.9"], "capability missing 18.6.22.9"
    assert cap["alarm_monitoring"]["direct_camera_rtsp_forbidden"]
    assert frame_jpeg_path(1, "x_sub").startswith("/media/w1/")
    print("[ok] capability + media snapshot path")

    admin = {
        "id": "accept-admin",
        "name": "Accept",
        "role": "Admin",
        "permissions": ["Events", "Live View"],
    }

    # Prefer an existing camera for synthetic events (no disconnect).
    cam = await camera_collection.find_one(
        {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
    )
    if not cam:
        # Synthetic camera doc for local-only accept without fleet
        oid = ObjectId()
        cam = {
            "_id": oid,
            "name": "synthetic_18_6_22_9",
            "display_name": "Synthetic 18.6.22.9",
            "camera_uid": f"syn_{oid}",
            "location_path": "Accept / Zone / Area",
            "camera_group": "accept_zone_area",
            "site": "Accept",
            "building": "Zone",
            "floor": "Area",
            "ptz": False,
            "worker_id": 1,
            "is_active": True,
            "ip_address": "127.0.0.1",
        }
        await camera_collection.insert_one(cam)
        synthetic_cam = True
    else:
        synthetic_cam = False

    cid = str(cam["_id"])
    z = resolve_affected_zone({"metadata": {}}, cam)
    assert z["geo_distance_used"] is False
    print(f"[ok] zone resolution source={z['source']} unknown={z.get('unknown')}")

    pref_ptz_id = ObjectId()
    await save_zone_camera_map(
        [
            {
                "zone": z.get("zone") or "Accept / Zone / Area",
                "preferred_camera_id": cid,
                "prefer_ptz": True,
            }
        ]
    )
    print("[ok] zone→camera map saved")

    mon0 = await list_alarm_monitoring(user=admin, limit=5)
    # May already be ALARM from other open events — inject our own and verify presence.
    high = await create_event(
        camera_id=cid,
        source_type="manual_test",
        severity="critical",
        title="RDSO 18.6.22.9 synthetic HIGH",
        message="acceptance inject — safe",
        priority=5,
        metadata={"zone": "Accept-Gate", "acceptance": "18.6.22.9"},
    )
    low = await create_event(
        camera_id=cid,
        source_type="manual_test",
        severity="info",
        title="RDSO 18.6.22.9 synthetic LOW",
        message="acceptance inject — safe",
        priority=1,
        metadata={"zone": "Accept-Gate", "acceptance": "18.6.22.9"},
    )
    print(f"[ok] injected events {high['id']} P5, {low['id']} P1")

    mon = await list_alarm_monitoring(user=admin, limit=20)
    assert mon["monitoring_state"] == STATE_ALARM
    ids = [i["event_id"] for i in mon["items"]]
    assert high["id"] in ids and low["id"] in ids
    hi_idx = ids.index(high["id"])
    lo_idx = ids.index(low["id"])
    assert hi_idx < lo_idx, "higher priority must sort before lower"
    item = mon["items"][hi_idx]
    assert item["via_vms_only"] and item["direct_camera_rtsp"] is False
    assert item["snapshot"] and str(item["snapshot"]["frame_jpeg_path"]).startswith("/media/")
    assert item["affected_zone"]["zone"] == "Accept-Gate"
    print("[ok] ALARM state, priority order, zone, VMS snapshot path")

    await recover_alarm_monitoring(high["id"], admin)
    mon2 = await list_alarm_monitoring(user=admin, limit=20)
    ids2 = [i["event_id"] for i in mon2["items"]]
    assert high["id"] not in ids2
    assert low["id"] in ids2
    print("[ok] recover high → next queued (low) remains")

    await recover_alarm_monitoring(low["id"], admin)
    # Clean any leftover acceptance events
    await events_collection.update_many(
        {"metadata.acceptance": "18.6.22.9"},
        {"$set": {"metadata.monitoring_recovered": True}},
    )
    mon3 = await list_alarm_monitoring(user=admin, limit=20)
    # If no other open alarms, back to MONITORING
    if mon3["active_count"] == 0:
        assert mon3["monitoring_state"] == STATE_MONITORING
        print("[ok] MONITORING after recovery")
    else:
        print(f"[ok] recovery done; other active alarms remain count={mon3['active_count']}")

    if synthetic_cam:
        await camera_collection.delete_one({"_id": cam["_id"]})
        print("[ok] removed synthetic camera")

    _ = pref_ptz_id  # reserved if PTZ sibling tests expand
    print("[PASS] RDSO 18.6.22.9 acceptance")
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run()))
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
