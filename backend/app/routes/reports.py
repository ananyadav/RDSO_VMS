"""RDSO 18.1.15 — Alarm / Incident / Operator Log reports (reuse events + audit_logs)."""

from __future__ import annotations

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.report_service import (
    ALARM_CSV_FIELDS,
    INCIDENT_CSV_FIELDS,
    OPERATOR_CSV_FIELDS,
    build_alarm_report,
    build_incident_report,
    build_operator_log_report,
    rows_to_csv,
)


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


def _wants_csv(request: web.Request) -> bool:
    q = request.rel_url.query
    fmt = (q.get("format") or "").strip().lower()
    if fmt == "csv":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "text/csv" in accept and "application/json" not in accept


def _csv_response(filename: str, body: str) -> web.Response:
    return web.Response(
        text=body,
        content_type="text/csv",
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


async def alarm_report_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    as_csv = _wants_csv(request)
    data = await build_alarm_report(
        user,
        camera_id=q.get("camera_id"),
        source_type=q.get("source_type"),
        severity=q.get("severity"),
        status=q.get("status"),
        acknowledged=_bool_query(q.get("acknowledged")),
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_csv=as_csv,
    )
    if as_csv:
        return _csv_response("alarm-report.csv", rows_to_csv(data["items"], ALARM_CSV_FIELDS))
    return web.json_response(data)


async def incident_report_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    as_csv = _wants_csv(request)
    data = await build_incident_report(
        user,
        camera_id=q.get("camera_id"),
        status=q.get("status"),
        severity=q.get("severity"),
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_csv=as_csv,
    )
    if as_csv:
        return _csv_response(
            "incident-report.csv", rows_to_csv(data["items"], INCIDENT_CSV_FIELDS)
        )
    return web.json_response(data)


async def operator_logs_report_endpoint(request: web.Request) -> web.Response:
    """Operator activity from audit_logs — Admin / SuperAdmin (report surface)."""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    as_csv = _wants_csv(request)
    data = await build_operator_log_report(
        actor_user_id=q.get("user") or q.get("actor_user_id"),
        action=q.get("action"),
        camera_id=q.get("camera_id") or q.get("camera"),
        resource_id=q.get("resource_id"),
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_csv=as_csv,
    )
    if as_csv:
        return _csv_response(
            "operator-logs.csv", rows_to_csv(data["items"], OPERATOR_CSV_FIELDS)
        )
    return web.json_response(data)


def setup_report_routes(app: web.Application) -> None:
    app.router.add_get("/api/reports/alarms", alarm_report_endpoint)
    app.router.add_get("/api/reports/incidents", incident_report_endpoint)
    app.router.add_get("/api/reports/operator-logs", operator_logs_report_endpoint)
