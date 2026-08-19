"""PTZ control routes (Hikvision ISAPI, ONVIF, Dahua)."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_camera_access, has_permission, PERMISSION_LIVE_VIEW
from app.core.auth_context import get_effective_user
from app.services.audit_service import ACTION_PTZ_PAN, ACTION_PTZ_STOP, ACTION_PTZ_TILT, ACTION_PTZ_ZOOM, write_audit
from app.services.camera_access import is_admin
from app.services.camera_identity import get_camera_by_ref
from app.services.ptz_control import (
    delete_preset,
    goto_preset,
    list_presets,
    ptz_capabilities,
    ptz_continuous,
    ptz_move_direction,
    ptz_stop,
    set_preset,
)

logger = logging.getLogger(__name__)


async def _require_live_camera(request: web.Request, camera_id: str) -> tuple[dict | None, web.Response | None]:
    user = await get_effective_user(request)
    if user is None:
        return None, web.json_response({"error": "Authentication required"}, status=401)
    if not is_admin(user) and not has_permission(user, PERMISSION_LIVE_VIEW):
        return None, web.json_response({"error": "Live View permission required"}, status=403)

    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return None, denied

    camera = await get_camera_by_ref(camera_id)
    if camera is None:
        return None, web.json_response({"error": "Camera not found"}, status=404)
    if camera.get("is_active") is False:
        return None, web.json_response({"error": "Camera is disabled"}, status=400)
    if not camera.get("ptz"):
        return None, web.json_response({"error": "Camera is not marked as PTZ"}, status=400)
    return camera, None


async def ptz_list_cameras(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if user is None:
        return web.json_response({"error": "Authentication required"}, status=401)
    if not is_admin(user) and not has_permission(user, PERMISSION_LIVE_VIEW):
        return web.json_response({"error": "Live View permission required"}, status=403)

    from app.services.camera_service import get_camera_info

    cameras = await get_camera_info(request)
    ptz_cams = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "displayName": c.get("displayName"),
            "online": c.get("online"),
            "ip_address": c.get("ip_address"),
        }
        for c in cameras
        if c.get("ptz")
    ]
    return web.json_response({"cameras": ptz_cams})


async def ptz_move(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err

    try:
        body = await request.json()
    except Exception:
        body = {}

    speed = int(body.get("speed") or 2)
    direction = body.get("direction")
    if direction:
        result = await ptz_move_direction(camera, str(direction), speed=speed)
    else:
        pan = int(body.get("pan") or 0)
        tilt = int(body.get("tilt") or 0)
        zoom = int(body.get("zoom") or 0)
        result = await ptz_continuous(camera, pan=pan, tilt=tilt, zoom=zoom)

    if not result.get("ok"):
        return web.json_response(result, status=502)

    actor = await get_effective_user(request)
    pan = int(body.get("pan") or 0)
    tilt = int(body.get("tilt") or 0)
    zoom = int(body.get("zoom") or 0)
    direction_l = str(direction or "").lower()
    if direction_l in ("left", "right") or pan:
        await write_audit(
            action=ACTION_PTZ_PAN,
            actor=actor,
            resource_type="camera",
            resource_id=camera_id,
            request=request,
            success=True,
            metadata={"direction": direction_l or None, "pan": pan},
        )
    if direction_l in ("up", "down") or tilt:
        await write_audit(
            action=ACTION_PTZ_TILT,
            actor=actor,
            resource_type="camera",
            resource_id=camera_id,
            request=request,
            success=True,
            metadata={"direction": direction_l or None, "tilt": tilt},
        )
    if direction_l in ("in", "out", "zoom_in", "zoom_out") or zoom:
        await write_audit(
            action=ACTION_PTZ_ZOOM,
            actor=actor,
            resource_type="camera",
            resource_id=camera_id,
            request=request,
            success=True,
            metadata={"direction": direction_l or None, "zoom": zoom},
        )
    return web.json_response({"ok": True})


async def ptz_stop_handler(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await ptz_stop(camera)
    if not result.get("ok"):
        return web.json_response(result, status=502)
    actor = await get_effective_user(request)
    await write_audit(
        action=ACTION_PTZ_STOP,
        actor=actor,
        resource_type="camera",
        resource_id=camera_id,
        request=request,
        success=True,
    )
    return web.json_response({"ok": True})


async def ptz_presets_list(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await list_presets(camera)
    status = 200 if result.get("ok") else 502
    return web.json_response(result, status=status)


async def ptz_preset_goto(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    preset_id = request.match_info["presetId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await goto_preset(camera, int(preset_id))
    if not result.get("ok"):
        return web.json_response(result, status=502)
    return web.json_response({"ok": True})


async def ptz_preset_set(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    preset_id = request.match_info["presetId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str(body.get("name") or f"Preset {preset_id}")
    result = await set_preset(camera, int(preset_id), name)
    if not result.get("ok"):
        return web.json_response(result, status=502)
    return web.json_response({"ok": True})


async def ptz_preset_delete(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    preset_id = request.match_info["presetId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await delete_preset(camera, int(preset_id))
    if not result.get("ok"):
        return web.json_response(result, status=502)
    return web.json_response({"ok": True})


async def ptz_status(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await ptz_capabilities(camera)
    return web.json_response(result, status=200 if result.get("ok") else 502)


def setup_ptz_routes(app: web.Application) -> None:
    app.router.add_get("/api/ptz/cameras", ptz_list_cameras)
    app.router.add_post("/api/ptz/{cameraId}/move", ptz_move)
    app.router.add_post("/api/ptz/{cameraId}/stop", ptz_stop_handler)
    app.router.add_get("/api/ptz/{cameraId}/presets", ptz_presets_list)
    app.router.add_get("/api/ptz/{cameraId}/status", ptz_status)
    app.router.add_post("/api/ptz/{cameraId}/presets/{presetId}/goto", ptz_preset_goto)
    app.router.add_put("/api/ptz/{cameraId}/presets/{presetId}", ptz_preset_set)
    app.router.add_delete("/api/ptz/{cameraId}/presets/{presetId}", ptz_preset_delete)
