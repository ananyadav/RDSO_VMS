"""RDSO 18.6.22.9 — CCC alarm monitoring + zone camera map + snapshot descriptor."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import (
    deny_unless_admin,
    deny_unless_camera_access,
    deny_unless_events_permission,
    has_live_view,
    require_user,
)
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.camera_access import is_admin
from app.services.ccc_alarm_monitoring_service import (
    get_zone_camera_map,
    list_alarm_monitoring,
    recover_alarm_monitoring,
    save_zone_camera_map,
)
from app.services.ccc_vms_source import get_ccc_source

logger = logging.getLogger(__name__)

ACTION_ZONE_MAP = "CCC_ZONE_CAMERA_MAP_UPDATED"
ACTION_ALARM_RECOVER = "CCC_ALARM_MONITORING_RECOVERED"


async def ccc_alarm_monitoring_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        limit = int(request.query.get("limit") or 20)
    except ValueError:
        limit = 20
    data = await list_alarm_monitoring(user=user, limit=limit)
    return web.json_response(data)


async def ccc_alarm_recover_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    event_id = request.match_info.get("eventId") or ""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    updated = await recover_alarm_monitoring(
        event_id, user, reason=str(body.get("reason") or "operator_reset")
    )
    if not updated:
        return web.json_response({"error": "Not found"}, status=404)
    await write_audit(
        action=ACTION_ALARM_RECOVER,
        actor=user,
        resource_type="event",
        resource_id=updated.get("id"),
        request=request,
        success=True,
        metadata={"monitoring_recovered": True},
    )
    # Return refreshed monitoring queue (next alarm if any).
    queue = await list_alarm_monitoring(user=user, limit=20)
    return web.json_response(
        {
            "ok": True,
            "event": updated,
            "monitoring": queue,
            "rdso_18_6_22_9": True,
        }
    )


async def ccc_zone_camera_map_get(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    return web.json_response(await get_zone_camera_map())


async def ccc_zone_camera_map_put(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    mappings = body.get("mappings")
    if not isinstance(mappings, list):
        return web.json_response({"error": "mappings must be a list"}, status=400)
    try:
        data = await save_zone_camera_map(mappings)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_ZONE_MAP,
        actor=user,
        resource_type="ccc_zone_camera_map",
        resource_id="ccc_zone_camera_map",
        request=request,
        success=True,
        metadata={"count": len(data.get("mappings") or [])},
    )
    return web.json_response({"ok": True, **data})


async def ccc_camera_snapshot_endpoint(request: web.Request) -> web.Response:
    """
    Authenticated on-demand snapshot descriptor (relative /media frame.jpeg).
    Does not connect to the camera; Nginx → go2rtc serves the JPEG.
    """
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    if not is_admin(user) and not has_live_view(user):
        return web.json_response({"error": "Live View permission required"}, status=403)
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    source_id = (request.query.get("source") or "local").strip() or "local"
    try:
        source = get_ccc_source(source_id)
        media = await source.client_media_for(camera_id, user)
    except KeyError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except LookupError:
        return web.json_response({"error": "Camera not found"}, status=404)
    except PermissionError:
        return web.json_response({"error": "Forbidden"}, status=403)
    snap = media.get("snapshot") if isinstance(media, dict) else None
    if not isinstance(snap, dict) or not snap.get("frame_jpeg_path"):
        return web.json_response(
            {"error": "Snapshot path unavailable", "via_vms_only": True}, status=404
        )
    return web.json_response(
        {
            "camera_id": camera_id,
            "snapshot": snap,
            "via_vms_go2rtc": True,
            "direct_camera": False,
            "direct_camera_rtsp": False,
            "rdso_18_6_22_9": True,
        }
    )


def setup_ccc_alarm_monitoring_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/alarm-monitoring", ccc_alarm_monitoring_endpoint)
    app.router.add_post(
        "/api/ccc/alarm-monitoring/{eventId}/recover", ccc_alarm_recover_endpoint
    )
    app.router.add_get("/api/ccc/zone-camera-map", ccc_zone_camera_map_get)
    app.router.add_put("/api/ccc/admin/zone-camera-map", ccc_zone_camera_map_put)
    app.router.add_get(
        "/api/ccc/cameras/{cameraId}/snapshot", ccc_camera_snapshot_endpoint
    )
