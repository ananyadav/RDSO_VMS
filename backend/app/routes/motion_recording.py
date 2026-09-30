"""API routes for RDSO 18.3.14 motion/activity-based recording."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import (
    deny_unless_admin,
    deny_unless_camera_access,
    deny_unless_super_admin,
)
from app.core.auth_context import get_effective_user
from app.services.audit_service import ACTION_RECORDING_CONFIG_CHANGED, write_audit

logger = logging.getLogger(__name__)


async def motion_settings_get_endpoint(request: web.Request):
    """GET /api/cameras/{cameraId}/motion-recording"""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    from app.services.motion_recording_config import get_motion_recording_settings

    try:
        data = await get_motion_recording_settings(camera_id)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    return web.json_response(data)


async def motion_settings_put_endpoint(request: web.Request):
    """PUT /api/cameras/{cameraId}/motion-recording"""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    # Accept nested config or flat body
    patch = body.get("config") if isinstance(body.get("config"), dict) else body
    from app.services.motion_recording_config import update_motion_recording_settings

    try:
        data = await update_motion_recording_settings(camera_id, patch)
    except ValueError as exc:
        msg = str(exc)
        status = 404 if "not found" in msg.lower() else 400
        return web.json_response({"error": msg, "supported": False}, status=status)

    actor = await get_effective_user(request)
    await write_audit(
        action=ACTION_RECORDING_CONFIG_CHANGED,
        actor=actor,
        resource_type="camera",
        resource_id=camera_id,
        resource_label="motion_recording",
        request=request,
        success=True,
        metadata={"operation": "motion_recording_settings", "config": data.get("config")},
    )
    return web.json_response(data)


async def motion_capability_endpoint(request: web.Request):
    """GET /api/cameras/{cameraId}/motion-recording/capability"""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    from app.services.camera_identity import get_camera_by_ref
    from app.services.motion_capability import detect_motion_capability

    cam = await get_camera_by_ref(camera_id)
    if not cam:
        return web.json_response({"error": "Camera not found"}, status=404)
    cap = await detect_motion_capability(cam)
    return web.json_response(cap)


async def motion_activity_ingest_endpoint(request: web.Request):
    """POST /api/cameras/{cameraId}/motion-activity

    Body: {active: true|false, source?: string}
    Authenticated admin ingest for camera motion edges (tests / adapters).
    Also forwards a normalized motion alarm signal when active=true.
    """
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    active = body.get("active")
    if active is None:
        return web.json_response({"error": "active boolean required"}, status=400)
    active_b = bool(active) if not isinstance(active, str) else active.strip().lower() in (
        "1",
        "true",
        "yes",
    )
    source = str(body.get("source") or "api_ingest").strip() or "api_ingest"

    from app.services.motion_recording_controller import (
        get_activity_state,
        notify_motion_activity,
    )

    result = await notify_motion_activity(camera_id, active=active_b, source=source)

    alarm_result = None
    # Rising-edge emit into existing alarm engine (default on for active=true)
    emit = body.get("emit_alarm")
    if emit is None:
        emit = active_b
    if emit:
        from app.services.camera_event_inputs import emit_motion_alarm_if_rising
        from app.services.camera_identity import get_camera_by_ref

        cam = await get_camera_by_ref(camera_id)
        if cam:
            alarm_result = await emit_motion_alarm_if_rising(
                cam, active_b, source=source
            )
        else:
            alarm_result = {"emitted": False, "reason": "camera_not_found"}

    return web.json_response(
        {
            **result,
            "state": get_activity_state(camera_id),
            "alarm": alarm_result,
        }
    )


async def motion_state_get_endpoint(request: web.Request):
    """GET /api/cameras/{cameraId}/motion-recording/state"""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    from app.services.motion_recording_controller import get_activity_state

    return web.json_response(get_activity_state(camera_id))


def setup_motion_recording_routes(app: web.Application) -> None:
    app.router.add_get(
        "/api/cameras/{cameraId}/motion-recording", motion_settings_get_endpoint
    )
    app.router.add_put(
        "/api/cameras/{cameraId}/motion-recording", motion_settings_put_endpoint
    )
    app.router.add_get(
        "/api/cameras/{cameraId}/motion-recording/capability",
        motion_capability_endpoint,
    )
    app.router.add_get(
        "/api/cameras/{cameraId}/motion-recording/state",
        motion_state_get_endpoint,
    )
    app.router.add_post(
        "/api/cameras/{cameraId}/motion-activity",
        motion_activity_ingest_endpoint,
    )
