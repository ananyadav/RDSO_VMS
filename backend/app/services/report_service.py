"""RDSO 18.1.15 — reporting rows from existing events + audit_logs (no duplicate stores)."""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

from app.services.audit_service import query_audit_logs, sanitize_metadata
from app.services.event_service import list_events


REPORT_PAGE_MAX = 200
REPORT_CSV_MAX = 2000


def event_alarm_state(event: dict) -> str:
    """Acknowledge / recovery / display-reset state for alarm reports."""
    if event.get("acknowledged"):
        return "acknowledged"
    md = event.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    if md.get("display_reset") or md.get("display_reset_at"):
        return "display_reset"
    if md.get("signal_restored") or md.get("recovered_at"):
        return "recovered"
    return str(event.get("status") or "open")


def alarm_report_row(event: dict) -> dict[str, Any]:
    md = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    return {
        "event_id": event.get("id"),
        "occurred_at": event.get("occurred_at"),
        "camera_id": event.get("camera_id") or "",
        "camera_uid": event.get("camera_uid") or "",
        "source_type": event.get("source_type") or "",
        "severity": event.get("severity") or "",
        "status": event.get("status") or "",
        "acknowledged": bool(event.get("acknowledged")),
        "alarm_state": event_alarm_state(event),
        "title": event.get("title") or "",
        "recovered_at": md.get("recovered_at") or "",
        "display_reset_at": md.get("display_reset_at") or "",
    }


def incident_report_row(event: dict) -> dict[str, Any]:
    """Incident view of the same persisted event (no fabricated fields)."""
    return {
        "event_id": event.get("id"),
        "occurred_at": event.get("occurred_at"),
        "camera_id": event.get("camera_id") or "",
        "camera_uid": event.get("camera_uid") or "",
        "title": event.get("title") or "",
        "message": event.get("message") or "",
        "source_type": event.get("source_type") or "",
        "severity": event.get("severity") or "",
        "status": event.get("status") or "",
        "acknowledged": bool(event.get("acknowledged")),
        "acknowledged_at": event.get("acknowledged_at") or "",
        "alarm_state": event_alarm_state(event),
    }


def operator_log_row(item: dict) -> dict[str, Any]:
    meta = sanitize_metadata(item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
    return {
        "id": item.get("id"),
        "timestamp": item.get("timestamp"),
        "actor_user_id": item.get("actor_user_id") or "",
        "actor_username": item.get("actor_username") or "",
        "actor_role": item.get("actor_role") or "",
        "action": item.get("action") or "",
        "resource_type": item.get("resource_type") or "",
        "resource_id": item.get("resource_id") or "",
        "resource_label": item.get("resource_label") or "",
        "camera_id": meta.get("camera_id") or (
            item.get("resource_id") if item.get("resource_type") == "camera" else ""
        ),
        "success": bool(item.get("success")),
        "status": item.get("status") or "",
    }


def rows_to_csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {k: row.get(k, "") for k in fieldnames}
        writer.writerow(clean)
    return buf.getvalue()


ALARM_CSV_FIELDS = [
    "event_id",
    "occurred_at",
    "camera_id",
    "camera_uid",
    "source_type",
    "severity",
    "status",
    "acknowledged",
    "alarm_state",
    "title",
    "recovered_at",
    "display_reset_at",
]

INCIDENT_CSV_FIELDS = [
    "event_id",
    "occurred_at",
    "camera_id",
    "camera_uid",
    "title",
    "message",
    "source_type",
    "severity",
    "status",
    "acknowledged",
    "acknowledged_at",
    "alarm_state",
]

OPERATOR_CSV_FIELDS = [
    "id",
    "timestamp",
    "actor_user_id",
    "actor_username",
    "actor_role",
    "action",
    "resource_type",
    "resource_id",
    "resource_label",
    "camera_id",
    "success",
    "status",
]


async def build_alarm_report(
    user: dict,
    *,
    camera_id: Optional[str] = None,
    source_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_csv: bool = False,
) -> dict:
    cap = REPORT_CSV_MAX if for_csv else REPORT_PAGE_MAX
    data = await list_events(
        user,
        camera_id=camera_id,
        source_type=source_type,
        severity=severity,
        status=status,
        acknowledged=acknowledged,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=min(int(limit or 50), cap),
        offset=offset,
    )
    rows = [alarm_report_row(ev) for ev in data.get("items") or []]
    return {
        "report": "alarm",
        "items": rows,
        "total": data.get("total", 0),
        "limit": data.get("limit"),
        "offset": data.get("offset"),
    }


async def build_incident_report(
    user: dict,
    *,
    camera_id: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_csv: bool = False,
) -> dict:
    """Incidents = same persisted events collection; columns emphasize incident detail."""
    cap = REPORT_CSV_MAX if for_csv else REPORT_PAGE_MAX
    data = await list_events(
        user,
        camera_id=camera_id,
        severity=(severity or None),
        status=status,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=min(int(limit or 50), cap),
        offset=offset,
    )
    rows = [incident_report_row(ev) for ev in data.get("items") or []]
    return {
        "report": "incident",
        "items": rows,
        "total": data.get("total", 0),
        "limit": data.get("limit"),
        "offset": data.get("offset"),
    }


async def build_operator_log_report(
    *,
    actor_user_id: Optional[str] = None,
    action: Optional[str] = None,
    camera_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_csv: bool = False,
) -> dict:
    cap = REPORT_CSV_MAX if for_csv else REPORT_PAGE_MAX
    data = await query_audit_logs(
        actor_user_id=actor_user_id,
        action=action,
        camera_id=camera_id,
        resource_id=resource_id,
        start=from_ts,
        end=to_ts,
        limit=min(int(limit or 50), cap),
        offset=offset,
    )
    rows = [operator_log_row(item) for item in data.get("items") or []]
    return {
        "report": "operator_logs",
        "items": rows,
        "total": data.get("total", 0),
        "limit": data.get("limit"),
        "offset": data.get("offset"),
    }
