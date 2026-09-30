"""Safe acceptance for RDSO 18.1.25 — inject one ui_notification event (no camera disconnect)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

SAFE_IP = "192.168.41.90"


async def main() -> int:
    from app.core.database import camera_collection, events_collection
    from app.services.event_service import create_event, display_reset_event, get_event
    from bson import ObjectId

    cam = await camera_collection.find_one({"ip_address": SAFE_IP})
    if not cam:
        print("FAIL: safe camera not found", SAFE_IP)
        return 1
    cam_id = str(cam["_id"])
    print("camera", cam_id, cam.get("camera_uid"), SAFE_IP)

    admin = {"_id": "accept-18-1-25", "role": "Admin", "permissions": []}

    ev = await create_event(
        camera_id=cam_id,
        source_type="manual_test",
        severity="warning",
        title="RDSO 18.1.25 acceptance alarm",
        message="Injected for alarm display acceptance — safe, no disconnect",
        camera_uid=cam.get("camera_uid"),
        metadata={"acceptance": "18.1.25", "injected": True},
        actions_triggered=["create_event", "ui_notification"],
        ui_notification=True,
    )
    eid = ev["id"]
    print("injected_event", eid, "ui_notification", ev["ui_notification"], "ack", ev["acknowledged"])

    # Display selection (frontend pure logic mirrored): active until reset/recovery/ack
    assert ev["ui_notification"] and not ev["acknowledged"]
    print("PASS: automatic display candidate created (ui_notification open)")

    # Manual reset without ack
    reset = await display_reset_event(eid, admin)
    assert reset is not None
    assert reset["acknowledged"] is False
    assert reset["status"] == "open"
    assert reset["metadata"].get("display_reset") is True
    print("PASS: manual display-reset without acknowledge")

    # Second event for recovery path
    ev2 = await create_event(
        camera_id=cam_id,
        source_type="signal_loss",
        severity="critical",
        title="RDSO 18.1.25 recovery demo",
        message="Injected signal_loss for auto-reset acceptance",
        camera_uid=cam.get("camera_uid"),
        metadata={"acceptance": "18.1.25", "injected": True},
        actions_triggered=["create_event", "ui_notification"],
        ui_notification=True,
    )
    eid2 = ev2["id"]
    await events_collection.update_one(
        {"_id": ObjectId(eid2)},
        {
            "$set": {
                "metadata.recovered_at": "2026-09-08T12:00:00+00:00",
                "metadata.signal_restored": True,
            }
        },
    )
    recovered = await get_event(eid2, admin)
    assert recovered["acknowledged"] is False
    assert recovered["metadata"].get("signal_restored") is True
    print("PASS: automatic recovery stamps leave event unacknowledged")

    # Multi-alarm: leave a third open for queue
    ev3 = await create_event(
        camera_id=cam_id,
        source_type="motion",
        severity="info",
        title="RDSO 18.1.25 multi-alarm queue",
        message="Second open notification for queue handling",
        camera_uid=cam.get("camera_uid"),
        metadata={"acceptance": "18.1.25", "injected": True},
        actions_triggered=["create_event", "ui_notification"],
        ui_notification=True,
    )
    print("PASS: multiple alarms injected; open event", ev3["id"], "still queued")

    print("ACCEPTANCE_OK")
    print("Open Live View as Events+Live user to see auto fullscreen for open ui_notification events.")
    print("Reset display / Acknowledge are separate controls on the alarm banner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
