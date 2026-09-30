"""RDSO 18.6.22.18 — CCC historical report API (CSV/JSON export)."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_historical_report_service import (
    build_ccc_activity_history,
    build_ccc_communications_history,
    build_ccc_compliance_history,
    build_ccc_event_history,
    build_ccc_incident_history,
    export_csv,
    export_json,
)
from app.services.ccc_incident_service import IncidentPermissionError

logger = logging.getLogger(__name__)

ACTION_CCC_REPORT_EXPORTED = "CCC_REPORT_EXPORTED"


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


def _format(request: web.Request) -> str:
    q = request.rel_url.query
    fmt = (q.get("format") or "").strip().lower()
    if fmt in ("csv", "json", "download-json"):
        return "json" if fmt == "download-json" else fmt
    accept = (request.headers.get("Accept") or "").lower()
    if "text/csv" in accept and "application/json" not in accept:
        return "csv"
    return "json"


def _pagination(request: web.Request) -> tuple[int, int] | web.Response:
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    return limit, offset


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


def _json_download(filename: str, body: str) -> web.Response:
    return web.Response(
        text=body,
        content_type="application/json",
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


async def _audit_export(request: web.Request, user, *, report: str, fmt: str, total: int) -> None:
    await write_audit(
        action=ACTION_CCC_REPORT_EXPORTED,
        actor=user,
        resource_type="ccc_report",
        resource_id=report,
        request=request,
        success=True,
        metadata={"format": fmt, "total": total, "export": True},
    )


async def _respond(request: web.Request, user, data: dict) -> web.Response:
    fmt = _format(request)
    report = str(data.get("report") or "ccc_report")
    # Explicit export formats
    qfmt = (request.rel_url.query.get("format") or "").strip().lower()
    if qfmt == "csv" or fmt == "csv":
        filename, body = export_csv(report, list(data.get("items") or []))
        await _audit_export(request, user, report=report, fmt="csv", total=int(data.get("returned") or 0))
        return _csv_response(filename, body)
    if qfmt in ("json", "download-json"):
        filename, body = export_json(report, data)
        await _audit_export(request, user, report=report, fmt="json", total=int(data.get("returned") or 0))
        return _json_download(filename, body)
    return web.json_response(data)


async def ccc_incident_history_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    page = _pagination(request)
    if isinstance(page, web.Response):
        return page
    limit, offset = page
    q = request.rel_url.query
    priority_raw = q.get("priority")
    priority = None
    if priority_raw not in (None, ""):
        try:
            priority = int(priority_raw)
        except ValueError:
            return web.json_response({"error": "Invalid priority"}, status=400)
    for_export = _format(request) in ("csv", "json") and bool(q.get("format"))
    try:
        data = await build_ccc_incident_history(
            user,
            status=q.get("status"),
            severity=q.get("severity"),
            priority=priority,
            assignee_group=q.get("assignee_group") or q.get("group"),
            assignee_user_id=q.get("assignee_user_id") or q.get("assignee"),
            location=q.get("location"),
            camera_id=q.get("camera_id") or q.get("camera"),
            from_ts=q.get("from") or q.get("start"),
            to_ts=q.get("to") or q.get("end"),
            limit=limit,
            offset=offset,
            for_export=for_export,
        )
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    return await _respond(request, user, data)


async def ccc_event_history_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    page = _pagination(request)
    if isinstance(page, web.Response):
        return page
    limit, offset = page
    q = request.rel_url.query
    for_export = bool(q.get("format"))
    data = await build_ccc_event_history(
        user,
        camera_id=q.get("camera_id") or q.get("camera"),
        source_type=q.get("source_type") or q.get("event_type"),
        severity=q.get("severity"),
        status=q.get("status"),
        acknowledged=_bool_query(q.get("acknowledged")),
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_export=for_export,
    )
    return await _respond(request, user, data)


async def ccc_activity_history_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    page = _pagination(request)
    if isinstance(page, web.Response):
        return page
    limit, offset = page
    q = request.rel_url.query
    for_export = bool(q.get("format"))
    data = await build_ccc_activity_history(
        actor_user_id=q.get("user") or q.get("actor_user_id"),
        action=q.get("action"),
        camera_id=q.get("camera_id") or q.get("camera"),
        resource_id=q.get("resource_id"),
        ccc_only=_bool_query(q.get("ccc_only")) is True,
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_export=for_export,
    )
    return await _respond(request, user, data)


async def ccc_comms_history_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    page = _pagination(request)
    if isinstance(page, web.Response):
        return page
    limit, offset = page
    q = request.rel_url.query
    for_export = bool(q.get("format"))
    data = await build_ccc_communications_history(
        incident_id=q.get("incident_id"),
        status=q.get("status"),
        from_ts=q.get("from") or q.get("start"),
        to_ts=q.get("to") or q.get("end"),
        limit=limit,
        offset=offset,
        for_export=for_export,
    )
    return await _respond(request, user, data)


async def ccc_compliance_history_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    page = _pagination(request)
    if isinstance(page, web.Response):
        return page
    limit, offset = page
    q = request.rel_url.query
    for_export = bool(q.get("format"))
    try:
        data = await build_ccc_compliance_history(
            user,
            state=q.get("state") or q.get("compliance_state"),
            from_ts=q.get("from") or q.get("start"),
            to_ts=q.get("to") or q.get("end"),
            limit=limit,
            offset=offset,
            for_export=for_export,
        )
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    return await _respond(request, user, data)


async def ccc_reports_capability_endpoint(request: web.Request) -> web.Response:
    try:
        from app.core.access_control import require_user

        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    from app.services.app_timezone import get_effective_app_timezone_name

    return web.json_response(
        {
            "rdso_18_6_22_18": True,
            "app_timezone": get_effective_app_timezone_name(),
            "formats": ["json", "csv"],
            "pdf": False,
            "duplicate_collections": False,
            "kinds": [
                "incidents",
                "events",
                "activity",
                "communications",
                "compliance",
            ],
            "paths": {
                "incidents": "/api/ccc/reports/incidents",
                "events": "/api/ccc/reports/events",
                "activity": "/api/ccc/reports/activity",
                "communications": "/api/ccc/reports/communications",
                "compliance": "/api/ccc/reports/compliance",
            },
        }
    )


def setup_ccc_report_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/reports", ccc_reports_capability_endpoint)
    app.router.add_get("/api/ccc/reports/incidents", ccc_incident_history_endpoint)
    app.router.add_get("/api/ccc/reports/events", ccc_event_history_endpoint)
    app.router.add_get("/api/ccc/reports/activity", ccc_activity_history_endpoint)
    app.router.add_get("/api/ccc/reports/communications", ccc_comms_history_endpoint)
    app.router.add_get("/api/ccc/reports/compliance", ccc_compliance_history_endpoint)
