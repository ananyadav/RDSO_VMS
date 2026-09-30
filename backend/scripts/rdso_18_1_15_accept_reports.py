"""Safe acceptance for RDSO 18.1.15 reporting — reuse events + audit_logs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> int:
    from app.core.database import camera_collection
    from app.services.event_service import create_event
    from app.services.report_service import (
        ALARM_CSV_FIELDS,
        build_alarm_report,
        build_incident_report,
        build_operator_log_report,
        rows_to_csv,
    )
    from app.services.audit_service import write_audit

    cam = await camera_collection.find_one({"ip_address": "192.168.41.90"})
    if not cam:
        print("FAIL: safe camera missing")
        return 1
    cam_id = str(cam["_id"])
    admin = {"_id": "accept-18-1-15", "name": "accept", "role": "Admin", "permissions": []}

    ev = await create_event(
        camera_id=cam_id,
        source_type="manual_test",
        severity="warning",
        title="RDSO 18.1.15 report acceptance",
        message="Safe injected event for reporting",
        camera_uid=cam.get("camera_uid"),
        metadata={"acceptance": "18.1.15", "password": "must-not-appear"},
        actions_triggered=["create_event"],
        ui_notification=True,
    )
    print("injected", ev["id"])

    alarm = await build_alarm_report(admin, camera_id=cam_id, limit=20, offset=0)
    assert alarm["total"] >= 1
    assert any(r["event_id"] == ev["id"] for r in alarm["items"])
    print("PASS: alarm report includes injected event")

    incident = await build_incident_report(admin, camera_id=cam_id, status="open", limit=20)
    assert any(r["event_id"] == ev["id"] for r in incident["items"])
    print("PASS: incident report includes injected event")

    await write_audit(
        action="EVENT_DISPLAY_RESET",
        actor=admin,
        resource_type="event",
        resource_id=ev["id"],
        success=True,
        metadata={"camera_id": cam_id, "token": "secret-token"},
    )
    logs = await build_operator_log_report(
        action="EVENT_DISPLAY_RESET", camera_id=cam_id, limit=20
    )
    assert logs["total"] >= 1
    print("PASS: operator logs report from audit_logs")

    csv_body = rows_to_csv(alarm["items"], ALARM_CSV_FIELDS)
    assert "password" not in csv_body.lower() or "must-not-appear" not in csv_body
    assert "event_id" in csv_body
    print("PASS: csv export body built without injected secret")
    print("ACCEPTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
