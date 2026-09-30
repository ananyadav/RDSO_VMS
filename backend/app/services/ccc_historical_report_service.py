"""RDSO 18.6.22.18 — CCC historical reports from existing stores (no duplicate collections).

Sources:
  - ccc_incidents (incident history)
  - events (event history) via list_events + ACL
  - audit_logs (operator/activity) via query_audit_logs
  - ccc_communications (internal receipts)
  - SOP compliance derived from incidents + workflows
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.app_timezone import get_app_timezone, get_effective_app_timezone_name
from app.services.audit_service import query_audit_logs, sanitize_metadata
from app.services.ccc_compliance_service import incident_compliance
from app.services.ccc_incident_service import (
    IncidentPermissionError,
    _assert_camera_acl,
    incident_to_public,
    incidents_collection,
    user_can_read_incidents,
)
from app.services.event_service import list_events
from app.services.report_service import (
    REPORT_CSV_MAX,
    REPORT_PAGE_MAX,
    alarm_report_row,
    operator_log_row,
    rows_to_csv,
)

CCC_REPORT_CSV_MAX = REPORT_CSV_MAX
CCC_REPORT_PAGE_MAX = REPORT_PAGE_MAX

SECRET_FIELD_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|rtsp|credential|authorization)",
    re.I,
)


def _cap(limit: int, *, for_export: bool) -> int:
    return max(1, min(int(limit or 50), CCC_REPORT_CSV_MAX if for_export else CCC_REPORT_PAGE_MAX))


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_local(value: Any) -> str:
    dt = _as_utc(value)
    if not dt:
        return ""
    return dt.astimezone(get_app_timezone()).isoformat()


def resolve_report_time_bounds(
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> tuple[Optional[datetime], Optional[datetime], str]:
    """Interpret from/to as ISO datetimes or YYYY-MM-DD calendar days in APP_TIMEZONE."""
    from app.services.app_timezone import local_day_bounds_utc

    tz_name = get_effective_app_timezone_name()
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    raw_from = (from_ts or "").strip()
    raw_to = (to_ts or "").strip()

    if raw_from:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_from):
            start, _ = local_day_bounds_utc(raw_from)
        else:
            start = _as_utc(raw_from)
    if raw_to:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_to):
            _, end = local_day_bounds_utc(raw_to)
        else:
            end = _as_utc(raw_to)
    return start, end, tz_name


def _iso_bound(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _meta(
    *,
    report: str,
    total: int,
    limit: int,
    offset: int,
    tz_name: str,
    extra: Optional[dict] = None,
) -> dict:
    out: dict[str, Any] = {
        "report": report,
        "rdso_18_6_22_18": True,
        "app_timezone": tz_name,
        "fake_statistics": False,
        "duplicate_collection": False,
        "persisted_original_records": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": 0,
        "empty": total == 0,
    }
    if extra:
        out.update(extra)
    return out


def redact_export_row(row: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in row.items():
        if SECRET_FIELD_RE.search(str(k)):
            continue
        if isinstance(v, str) and "rtsp://" in v.lower():
            clean[k] = "***"
            continue
        if isinstance(v, dict):
            clean[k] = sanitize_metadata(v)
        else:
            clean[k] = v
    return clean


CCC_INCIDENT_CSV_FIELDS = [
    "incident_id",
    "incident_time",
    "incident_time_local",
    "title",
    "status",
    "priority",
    "severity",
    "assignee_group",
    "assignee_user_id",
    "assignee_user_name",
    "location",
    "linked_camera_ids",
    "linked_event_ids",
    "sop_workflow_id",
    "sop_step_index",
]


def ccc_incident_report_row(pub: dict) -> dict[str, Any]:
    cams = pub.get("linked_camera_ids") or []
    evs = pub.get("linked_event_ids") or []
    return redact_export_row(
        {
            "incident_id": pub.get("id") or "",
            "incident_time": pub.get("incident_time") or "",
            "incident_time_local": _format_local(pub.get("incident_time")),
            "title": pub.get("title") or "",
            "status": pub.get("status") or "",
            "priority": pub.get("priority"),
            "severity": pub.get("severity") or "",
            "assignee_group": pub.get("assignee_group") or "",
            "assignee_user_id": pub.get("assignee_user_id") or "",
            "assignee_user_name": pub.get("assignee_user_name") or "",
            "location": pub.get("location") or "",
            "linked_camera_ids": ",".join(str(x) for x in cams),
            "linked_event_ids": ",".join(str(x) for x in evs),
            "sop_workflow_id": pub.get("sop_workflow_id") or "",
            "sop_step_index": pub.get("sop_step_index") or 0,
        }
    )


async def build_ccc_incident_history(
    user: dict,
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    priority: Optional[int] = None,
    assignee_group: Optional[str] = None,
    assignee_user_id: Optional[str] = None,
    location: Optional[str] = None,
    camera_id: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_export: bool = False,
) -> dict[str, Any]:
    if not user_can_read_incidents(user):
        raise IncidentPermissionError("Incident read permission required")

    start, end, tz_name = resolve_report_time_bounds(from_ts, to_ts)
    limit_n = _cap(limit, for_export=for_export)
    offset_n = max(0, int(offset or 0))

    query: dict[str, Any] = {}
    if status:
        query["status"] = status.strip().lower()
    if severity:
        query["severity"] = severity.strip().lower()
    if priority is not None:
        query["priority"] = int(priority)
    if assignee_group:
        query["assignee_group"] = assignee_group.strip()
    if assignee_user_id:
        query["assignee_user_id"] = assignee_user_id.strip()
    if location:
        query["location"] = {"$regex": location.strip(), "$options": "i"}
    if camera_id:
        query["linked_camera_ids"] = camera_id.strip()

    rng: dict[str, Any] = {}
    if start:
        rng["$gte"] = start
    if end:
        rng["$lt"] = end
    if rng:
        query["incident_time"] = rng

    total = int(await incidents_collection.count_documents(query))
    cursor = (
        incidents_collection.find(query)
        .sort("incident_time", -1)
        .skip(offset_n)
        .limit(limit_n)
    )
    rows: list[dict[str, Any]] = []
    async for doc in cursor:
        cams = list(doc.get("linked_camera_ids") or [])
        try:
            await _assert_camera_acl(user, cams)
        except IncidentPermissionError:
            continue
        rows.append(ccc_incident_report_row(incident_to_public(doc)))

    meta = _meta(
        report="ccc_incident_history",
        total=total,
        limit=limit_n,
        offset=offset_n,
        tz_name=tz_name,
        extra={
            "from_utc": _iso_bound(start),
            "to_utc": _iso_bound(end),
            "camera_acl_applied": True,
        },
    )
    meta["items"] = rows
    meta["returned"] = len(rows)
    meta["empty"] = len(rows) == 0 and total == 0
    return meta


CCC_EVENT_CSV_FIELDS = [
    "event_id",
    "occurred_at",
    "occurred_at_local",
    "camera_id",
    "camera_uid",
    "source_type",
    "severity",
    "priority",
    "status",
    "acknowledged",
    "alarm_state",
    "title",
    "recovered_at",
    "display_reset_at",
]


def ccc_event_report_row(event: dict) -> dict[str, Any]:
    base = alarm_report_row(event)
    base["occurred_at_local"] = _format_local(event.get("occurred_at"))
    base["priority"] = event.get("priority") if event.get("priority") is not None else ""
    return redact_export_row(base)


async def build_ccc_event_history(
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
    for_export: bool = False,
) -> dict[str, Any]:
    start, end, tz_name = resolve_report_time_bounds(from_ts, to_ts)
    limit_n = _cap(limit, for_export=for_export)
    offset_n = max(0, int(offset or 0))
    data = await list_events(
        user,
        camera_id=camera_id,
        source_type=source_type,
        severity=severity,
        status=status,
        acknowledged=acknowledged,
        from_ts=_iso_bound(start) if start else None,
        to_ts=_iso_bound(end) if end else None,
        limit=limit_n,
        offset=offset_n,
    )
    rows = [ccc_event_report_row(ev) for ev in data.get("items") or []]
    meta = _meta(
        report="ccc_event_history",
        total=int(data.get("total") or 0),
        limit=limit_n,
        offset=offset_n,
        tz_name=tz_name,
        extra={
            "from_utc": _iso_bound(start),
            "to_utc": _iso_bound(end),
            "camera_acl_applied": True,
            "source": "events",
        },
    )
    meta["items"] = rows
    meta["returned"] = len(rows)
    meta["empty"] = meta["total"] == 0
    return meta


CCC_ACTIVITY_CSV_FIELDS = [
    "id",
    "timestamp",
    "timestamp_local",
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


def ccc_activity_report_row(item: dict) -> dict[str, Any]:
    row = operator_log_row(item)
    row["timestamp_local"] = _format_local(item.get("timestamp"))
    return redact_export_row(row)


async def build_ccc_activity_history(
    *,
    actor_user_id: Optional[str] = None,
    action: Optional[str] = None,
    camera_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    ccc_only: bool = False,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_export: bool = False,
) -> dict[str, Any]:
    start, end, tz_name = resolve_report_time_bounds(from_ts, to_ts)
    limit_n = _cap(limit, for_export=for_export)
    offset_n = max(0, int(offset or 0))

    if ccc_only and not action:
        data = await query_audit_logs(
            actor_user_id=actor_user_id,
            camera_id=camera_id,
            resource_id=resource_id,
            start=_iso_bound(start),
            end=_iso_bound(end),
            limit=min(limit_n * 3, 200),
            offset=offset_n,
        )
        filtered = [
            it
            for it in (data.get("items") or [])
            if str(it.get("action") or "").startswith("CCC_")
        ][:limit_n]
        rows = [ccc_activity_report_row(it) for it in filtered]
        meta = _meta(
            report="ccc_activity_history",
            total=int(data.get("total") or 0),
            limit=limit_n,
            offset=offset_n,
            tz_name=tz_name,
            extra={
                "from_utc": _iso_bound(start),
                "to_utc": _iso_bound(end),
                "ccc_only": True,
                "source": "audit_logs",
                "rbac": "admin_required",
                "note": "ccc_only filters action prefix CCC_ after query",
            },
        )
        meta["items"] = rows
        meta["returned"] = len(rows)
        meta["empty"] = len(rows) == 0
        return meta

    data = await query_audit_logs(
        actor_user_id=actor_user_id,
        action=action,
        camera_id=camera_id,
        resource_id=resource_id,
        start=_iso_bound(start),
        end=_iso_bound(end),
        limit=min(limit_n, 200),
        offset=offset_n,
    )
    rows = [ccc_activity_report_row(it) for it in data.get("items") or []]
    meta = _meta(
        report="ccc_activity_history",
        total=int(data.get("total") or 0),
        limit=limit_n,
        offset=offset_n,
        tz_name=tz_name,
        extra={
            "from_utc": _iso_bound(start),
            "to_utc": _iso_bound(end),
            "ccc_only": False,
            "source": "audit_logs",
            "rbac": "admin_required",
        },
    )
    meta["items"] = rows
    meta["returned"] = len(rows)
    meta["empty"] = meta["total"] == 0
    return meta


CCC_COMMS_CSV_FIELDS = [
    "id",
    "incident_id",
    "sent_at",
    "sent_at_local",
    "status",
    "subject",
    "recipient_user_id",
    "recipient_group",
    "delivered_at",
    "acknowledged_at",
    "channel",
    "external_delivery",
]


async def build_ccc_communications_history(
    *,
    incident_id: Optional[str] = None,
    status: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_export: bool = False,
) -> dict[str, Any]:
    from app.services.ccc_comms_service import comm_to_public, comms_collection

    start, end, tz_name = resolve_report_time_bounds(from_ts, to_ts)
    limit_n = _cap(limit, for_export=for_export)
    offset_n = max(0, int(offset or 0))
    query: dict[str, Any] = {}
    if incident_id:
        query["incident_id"] = incident_id.strip()
    if status:
        query["status"] = status.strip().lower()
    rng: dict[str, Any] = {}
    if start:
        rng["$gte"] = start
    if end:
        rng["$lt"] = end
    if rng:
        query["sent_at"] = rng

    total = int(await comms_collection.count_documents(query))
    cursor = (
        comms_collection.find(query).sort("sent_at", -1).skip(offset_n).limit(limit_n)
    )
    rows: list[dict[str, Any]] = []
    async for doc in cursor:
        pub = comm_to_public(doc)
        rows.append(
            redact_export_row(
                {
                    "id": pub["id"],
                    "incident_id": pub.get("incident_id") or "",
                    "sent_at": pub.get("sent_at") or "",
                    "sent_at_local": _format_local(pub.get("sent_at")),
                    "status": pub.get("status") or "",
                    "subject": pub.get("subject") or "",
                    "recipient_user_id": pub.get("recipient_user_id") or "",
                    "recipient_group": pub.get("recipient_group") or "",
                    "delivered_at": pub.get("delivered_at") or "",
                    "acknowledged_at": pub.get("acknowledged_at") or "",
                    "channel": pub.get("channel") or "ccc_internal",
                    "external_delivery": False,
                }
            )
        )

    meta = _meta(
        report="ccc_communications_history",
        total=total,
        limit=limit_n,
        offset=offset_n,
        tz_name=tz_name,
        extra={
            "from_utc": _iso_bound(start),
            "to_utc": _iso_bound(end),
            "external_delivery": False,
            "dmr_tetra": False,
            "source": "ccc_communications",
        },
    )
    meta["items"] = rows
    meta["returned"] = len(rows)
    meta["empty"] = total == 0
    return meta


CCC_COMPLIANCE_CSV_FIELDS = [
    "incident_id",
    "incident_time",
    "incident_time_local",
    "title",
    "status",
    "compliance_state",
    "completed_steps",
    "pending_steps",
    "total_steps",
    "overdue",
    "due_at",
    "assigned_group",
    "assigned_user_id",
]


async def build_ccc_compliance_history(
    user: dict,
    *,
    state: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    for_export: bool = False,
) -> dict[str, Any]:
    if not user_can_read_incidents(user):
        raise IncidentPermissionError("Incident read permission required")

    start, end, tz_name = resolve_report_time_bounds(from_ts, to_ts)
    limit_n = _cap(limit, for_export=for_export)
    offset_n = max(0, int(offset or 0))

    query: dict[str, Any] = {"sop_workflow_id": {"$exists": True, "$nin": [None, ""]}}
    rng: dict[str, Any] = {}
    if start:
        rng["$gte"] = start
    if end:
        rng["$lt"] = end
    if rng:
        query["incident_time"] = rng

    fetch_n = min(
        limit_n * 4,
        CCC_REPORT_CSV_MAX if for_export else CCC_REPORT_PAGE_MAX * 2,
    )
    cursor = (
        incidents_collection.find(query)
        .sort("incident_time", -1)
        .skip(offset_n)
        .limit(fetch_n)
    )
    rows: list[dict[str, Any]] = []
    scanned = 0
    async for doc in cursor:
        scanned += 1
        cams = list(doc.get("linked_camera_ids") or [])
        try:
            await _assert_camera_acl(user, cams)
        except IncidentPermissionError:
            continue
        comp = await incident_compliance(doc)
        st = str(comp.get("state") or "")
        if state and st != state.strip().lower():
            continue
        pub = incident_to_public(doc)
        rows.append(
            redact_export_row(
                {
                    "incident_id": pub["id"],
                    "incident_time": pub.get("incident_time") or "",
                    "incident_time_local": _format_local(pub.get("incident_time")),
                    "title": pub.get("title") or "",
                    "status": pub.get("status") or "",
                    "compliance_state": st,
                    "completed_steps": comp.get("completed_steps"),
                    "pending_steps": comp.get("pending_steps"),
                    "total_steps": comp.get("total_steps"),
                    "overdue": bool(comp.get("overdue")),
                    "due_at": comp.get("due_at") or "",
                    "assigned_group": comp.get("assigned_group") or "",
                    "assigned_user_id": comp.get("assigned_user_id") or "",
                }
            )
        )
        if len(rows) >= limit_n:
            break

    total_est = int(await incidents_collection.count_documents(query))
    meta = _meta(
        report="ccc_compliance_history",
        total=total_est,
        limit=limit_n,
        offset=offset_n,
        tz_name=tz_name,
        extra={
            "from_utc": _iso_bound(start),
            "to_utc": _iso_bound(end),
            "source": "ccc_incidents+sop_workflows",
            "derived": True,
            "scanned": scanned,
        },
    )
    meta["items"] = rows
    meta["returned"] = len(rows)
    meta["empty"] = total_est == 0
    return meta


def export_csv(report: str, items: list[dict[str, Any]]) -> tuple[str, str]:
    fields_map = {
        "ccc_incident_history": CCC_INCIDENT_CSV_FIELDS,
        "ccc_event_history": CCC_EVENT_CSV_FIELDS,
        "ccc_activity_history": CCC_ACTIVITY_CSV_FIELDS,
        "ccc_communications_history": CCC_COMMS_CSV_FIELDS,
        "ccc_compliance_history": CCC_COMPLIANCE_CSV_FIELDS,
    }
    fields = fields_map.get(report) or (list(items[0].keys()) if items else ["id"])
    filename = f"{report.replace('_', '-')}.csv"
    return filename, rows_to_csv(items, fields)


def export_json(report: str, payload: dict[str, Any]) -> tuple[str, str]:
    filename = f"{report.replace('_', '-')}.json"
    safe = dict(payload)
    safe["items"] = [
        redact_export_row(r) if isinstance(r, dict) else r
        for r in (payload.get("items") or [])
    ]
    return filename, json.dumps(safe, indent=2, default=str)
