"""RDSO 18.6 — Centralized Command Center (CCC) API routes."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_camera_access, require_user
from app.services.ccc_status import get_ccc_status_snapshot
from app.services.ccc_vms_adapter_errors import VmsAdapterError
from app.services.ccc_vms_source import (
    adapter_error_response,
    ccc_capability_public,
    count_external_vms_sources,
    get_ccc_source_async,
    list_ccc_sources,
)
from app.routes.ccc_vms_integrations import setup_ccc_vms_integration_routes
from app.routes.ccc_incidents import setup_ccc_incident_routes
from app.routes.ccc_dashboard import setup_ccc_dashboard_routes
from app.routes.ccc_reports import setup_ccc_report_routes
from app.routes.ccc_devices import setup_ccc_device_routes
from app.routes.ccc_alarm_monitoring import setup_ccc_alarm_monitoring_routes
from app.routes.ccc_health_reports import setup_ccc_health_report_routes
from app.routes.ccc_open_integrations import setup_ccc_open_integration_routes

logger = logging.getLogger(__name__)


async def ccc_capability_endpoint(request: web.Request) -> web.Response:
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(ccc_capability_public())


async def ccc_sources_endpoint(request: web.Request) -> web.Response:
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(
        {
            "items": list_ccc_sources(),
            "total": len(list_ccc_sources()),
            "external_adapters_registered": count_external_vms_sources(),
            "rdso_18_6": True,
        }
    )


async def ccc_cameras_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    source_id = (request.query.get("source") or "local").strip() or "local"
    try:
        limit = int(request.query.get("limit") or 100)
    except ValueError:
        limit = 100
    try:
        offset = int(request.query.get("offset") or 0)
    except ValueError:
        offset = 0
    q = (request.query.get("q") or "").strip()
    try:
        source = await get_ccc_source_async(source_id)
        payload = await source.list_cameras_public(user, limit=limit, offset=offset, q=q)
    except KeyError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except VmsAdapterError as exc:
        status, body = adapter_error_response(exc)
        return web.json_response(body, status=status)
    return web.json_response(payload)


async def ccc_client_media_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    camera_id = request.match_info.get("cameraId", "")
    source_id = (request.query.get("source") or "local").strip() or "local"
    if source_id == "local":
        denied = await deny_unless_camera_access(request, camera_id)
        if denied is not None:
            return denied
    else:
        from app.core.access_control import deny_unless_admin_or_live_view

        denied = await deny_unless_admin_or_live_view(request)
        if denied is not None:
            return denied
    try:
        source = await get_ccc_source_async(source_id)
        media = await source.client_media_for(camera_id, user)
    except KeyError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except LookupError:
        return web.json_response({"error": "Camera not found"}, status=404)
    except PermissionError:
        return web.json_response({"error": "Forbidden"}, status=403)
    except VmsAdapterError as exc:
        status, body = adapter_error_response(exc)
        return web.json_response(body, status=status)
    except Exception as exc:
        logger.exception("[ccc] client-media failed")
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response(media)


async def ccc_status_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    snap = await get_ccc_status_snapshot(user=user)
    return web.json_response(snap)


def setup_ccc_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/capability", ccc_capability_endpoint)
    app.router.add_get("/api/ccc/sources", ccc_sources_endpoint)
    app.router.add_get("/api/ccc/cameras", ccc_cameras_endpoint)
    app.router.add_get("/api/ccc/cameras/{cameraId}/client-media", ccc_client_media_endpoint)
    app.router.add_get("/api/ccc/status", ccc_status_endpoint)
    setup_ccc_incident_routes(app)
    setup_ccc_dashboard_routes(app)
    setup_ccc_report_routes(app)
    setup_ccc_device_routes(app)
    setup_ccc_alarm_monitoring_routes(app)
    setup_ccc_health_report_routes(app)
    setup_ccc_vms_integration_routes(app)
    setup_ccc_open_integration_routes(app)
