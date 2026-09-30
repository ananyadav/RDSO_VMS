"""RDSO 18.6.14 (non-GIS) — CCC Health Reports API."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_health_report_service import (
    build_ccc_health_report,
    export_health_csv,
)

logger = logging.getLogger(__name__)

ACTION_CCC_HEALTH_EXPORTED = "CCC_HEALTH_REPORT_EXPORTED"


async def ccc_health_reports_endpoint(request: web.Request) -> web.Response:
    # Authenticated; Events preferred for ops health; Live-only users get 403.
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
    try:
        data = await build_ccc_health_report(
            user=user,
            app=request.app,
            component_type=q.get("component_type"),
            status=q.get("status"),
            q=q.get("q") or "",
            limit=limit,
            offset=offset,
            for_export=False,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(data)


async def ccc_health_reports_export_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    fmt = (q.get("format") or "csv").strip().lower()
    try:
        limit = int(q.get("limit") or 500)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    try:
        data = await build_ccc_health_report(
            user=user,
            app=request.app,
            component_type=q.get("component_type"),
            status=q.get("status"),
            q=q.get("q") or "",
            limit=limit,
            offset=offset,
            for_export=True,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    await write_audit(
        action=ACTION_CCC_HEALTH_EXPORTED,
        actor=user,
        resource_type="ccc_health_report",
        resource_id="export",
        request=request,
        success=True,
        metadata={
            "format": fmt,
            "returned": data.get("returned"),
            "component_type": q.get("component_type"),
            "status": q.get("status"),
            "gis": False,
        },
    )

    if fmt == "json":
        return web.json_response(data)

    filename, body = export_health_csv(list(data.get("items") or []))
    return web.Response(
        text=body,
        content_type="text/csv",
        charset="utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def setup_ccc_health_report_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/health-reports", ccc_health_reports_endpoint)
    app.router.add_get("/api/ccc/health-reports/export", ccc_health_reports_export_endpoint)
