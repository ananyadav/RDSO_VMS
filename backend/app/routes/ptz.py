"""PTZ control routes (Hikvision ISAPI, ONVIF, Dahua)."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_camera_access, has_live_view
from app.core.auth_context import get_effective_user
from app.services.audit_service import (
    ACTION_PTZ_PAN,
    ACTION_PTZ_PATTERN_DELETE,
    ACTION_PTZ_PATTERN_RECORD_START,
    ACTION_PTZ_PATTERN_RECORD_STOP,
    ACTION_PTZ_PATTERN_SET,
    ACTION_PTZ_PATTERN_START,
    ACTION_PTZ_PATTERN_STOP,
    ACTION_PTZ_PRESET_DELETE,
    ACTION_PTZ_PRESET_GOTO,
    ACTION_PTZ_PRESET_SET,
    ACTION_PTZ_STOP,
    ACTION_PTZ_TILT,
    ACTION_PTZ_TOUR_DELETE,
    ACTION_PTZ_TOUR_SET,
    ACTION_PTZ_TOUR_START,
    ACTION_PTZ_TOUR_STOP,
    ACTION_PTZ_ZOOM,
    write_audit,
)
from app.services.camera_identity import get_camera_by_ref
from app.services.ptz_control import (
    delete_pattern,
    delete_preset,
    delete_tour,
    goto_preset,
    list_patterns,
    list_presets,
    list_tours,
    ptz_capabilities,
    ptz_continuous,
    ptz_move_direction,
    ptz_stop,
    record_pattern_start,
    record_pattern_stop,
    set_pattern,
    set_preset,
    set_tour,
    start_pattern,
    start_tour,
    stop_pattern,
    stop_tour,
)

logger = logging.getLogger(__name__)


async def _require_live_camera(request: web.Request, camera_id: str) -> tuple[dict | None, web.Response | None]:
    user = await get_effective_user(request)
    if user is None:
        return None, web.json_response({"error": "Authentication required"}, status=401)
    if not has_live_view(user):
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


def _result_response(result: dict, *, ok_status: int = 200) -> web.Response:
    if result.get("ok"):
        return web.json_response(result, status=ok_status)
    # Unsupported features: clear 501 so UI can disable without looking like a crash.
    if result.get("supported") is False or result.get("unsupported"):
        return web.json_response(result, status=501)
    return web.json_response(result, status=502)


async def _audit_ptz(
    request: web.Request,
    *,
    action: str,
    camera_id: str,
    success: bool,
    metadata: dict | None = None,
) -> None:
    actor = await get_effective_user(request)
    await write_audit(
        action=action,
        actor=actor,
        resource_type="camera",
        resource_id=camera_id,
        request=request,
        success=success,
        metadata={"camera_id": camera_id, **(metadata or {})},
    )


async def ptz_list_cameras(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if user is None:
        return web.json_response({"error": "Authentication required"}, status=401)
    if not has_live_view(user):
        return web.json_response({"error": "Live View permission required"}, status=403)

    from app.services.camera_service import get_camera_info

    cameras = await get_camera_info(request, filters={"ptz": True})
    ptz_cams = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "displayName": c.get("displayName"),
            "online": c.get("online"),
            "ip_address": c.get("ip_address"),
            "cameraUid": c.get("cameraUid") or c.get("camera_uid"),
            "workerId": c.get("workerId") or 1,
            "ptz": True,
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
    return _result_response(result)


async def ptz_preset_goto(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    preset_id = request.match_info["presetId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await goto_preset(camera, int(preset_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PRESET_GOTO,
            camera_id=camera_id,
            success=True,
            metadata={"preset_id": int(preset_id)},
        )
    return _result_response(result)


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
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PRESET_SET,
            camera_id=camera_id,
            success=True,
            metadata={"preset_id": int(preset_id), "preset_name": name[:80]},
        )
    return _result_response(result)


async def ptz_preset_delete(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    preset_id = request.match_info["presetId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await delete_preset(camera, int(preset_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PRESET_DELETE,
            camera_id=camera_id,
            success=True,
            metadata={"preset_id": int(preset_id)},
        )
    return _result_response(result)


async def ptz_tours_list(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await list_tours(camera)
    return _result_response(result)


async def ptz_tour_set(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    tour_id = request.match_info["tourId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str(body.get("name") or f"Tour {tour_id}")
    steps = body.get("steps") or []
    if not isinstance(steps, list):
        return web.json_response({"ok": False, "error": "steps must be a list"}, status=400)
    if not steps:
        return web.json_response({"ok": False, "error": "Tour requires at least one preset step"}, status=400)
    enabled = body.get("enabled", True) is not False
    result = await set_tour(camera, int(tour_id), name=name, steps=steps, enabled=enabled)
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_TOUR_SET,
            camera_id=camera_id,
            success=True,
            metadata={
                "tour_id": int(tour_id),
                "tour_name": name[:80],
                "step_count": len(steps),
                "enabled": enabled,
            },
        )
    return _result_response(result)


async def ptz_tour_delete(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    tour_id = request.match_info["tourId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await delete_tour(camera, int(tour_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_TOUR_DELETE,
            camera_id=camera_id,
            success=True,
            metadata={"tour_id": int(tour_id)},
        )
    return _result_response(result)


async def ptz_tour_start(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    tour_id = request.match_info["tourId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await start_tour(camera, int(tour_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_TOUR_START,
            camera_id=camera_id,
            success=True,
            metadata={"tour_id": int(tour_id)},
        )
    return _result_response(result)


async def ptz_tour_stop(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    tour_id = request.match_info["tourId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await stop_tour(camera, int(tour_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_TOUR_STOP,
            camera_id=camera_id,
            success=True,
            metadata={"tour_id": int(tour_id)},
        )
    return _result_response(result)


async def ptz_patterns_list(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await list_patterns(camera)
    return _result_response(result)


async def ptz_pattern_set(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = str(body.get("name") or f"Pattern {pattern_id}")
    result = await set_pattern(camera, int(pattern_id), name=name)
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_SET,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id), "pattern_name": name[:80]},
        )
    return _result_response(result)


async def ptz_pattern_delete(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await delete_pattern(camera, int(pattern_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_DELETE,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id)},
        )
    return _result_response(result)


async def ptz_pattern_start(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await start_pattern(camera, int(pattern_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_START,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id)},
        )
    return _result_response(result)


async def ptz_pattern_stop(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await stop_pattern(camera, int(pattern_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_STOP,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id)},
        )
    return _result_response(result)


async def ptz_pattern_record_start(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await record_pattern_start(camera, int(pattern_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_RECORD_START,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id)},
        )
    return _result_response(result)


async def ptz_pattern_record_stop(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    pattern_id = request.match_info["patternId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await record_pattern_stop(camera, int(pattern_id))
    if result.get("ok"):
        await _audit_ptz(
            request,
            action=ACTION_PTZ_PATTERN_RECORD_STOP,
            camera_id=camera_id,
            success=True,
            metadata={"pattern_id": int(pattern_id)},
        )
    return _result_response(result)


async def ptz_status(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    camera, err = await _require_live_camera(request, camera_id)
    if err is not None:
        return err
    result = await ptz_capabilities(camera)
    return _result_response(result)


def setup_ptz_routes(app: web.Application) -> None:
    app.router.add_get("/api/ptz/cameras", ptz_list_cameras)
    app.router.add_post("/api/ptz/{cameraId}/move", ptz_move)
    app.router.add_post("/api/ptz/{cameraId}/stop", ptz_stop_handler)
    app.router.add_get("/api/ptz/{cameraId}/presets", ptz_presets_list)
    app.router.add_get("/api/ptz/{cameraId}/status", ptz_status)
    app.router.add_post("/api/ptz/{cameraId}/presets/{presetId}/goto", ptz_preset_goto)
    app.router.add_put("/api/ptz/{cameraId}/presets/{presetId}", ptz_preset_set)
    app.router.add_delete("/api/ptz/{cameraId}/presets/{presetId}", ptz_preset_delete)
    app.router.add_get("/api/ptz/{cameraId}/tours", ptz_tours_list)
    app.router.add_put("/api/ptz/{cameraId}/tours/{tourId}", ptz_tour_set)
    app.router.add_delete("/api/ptz/{cameraId}/tours/{tourId}", ptz_tour_delete)
    app.router.add_post("/api/ptz/{cameraId}/tours/{tourId}/start", ptz_tour_start)
    app.router.add_post("/api/ptz/{cameraId}/tours/{tourId}/stop", ptz_tour_stop)
    app.router.add_get("/api/ptz/{cameraId}/patterns", ptz_patterns_list)
    app.router.add_put("/api/ptz/{cameraId}/patterns/{patternId}", ptz_pattern_set)
    app.router.add_delete("/api/ptz/{cameraId}/patterns/{patternId}", ptz_pattern_delete)
    app.router.add_post("/api/ptz/{cameraId}/patterns/{patternId}/start", ptz_pattern_start)
    app.router.add_post("/api/ptz/{cameraId}/patterns/{patternId}/stop", ptz_pattern_stop)
    app.router.add_post(
        "/api/ptz/{cameraId}/patterns/{patternId}/record-start",
        ptz_pattern_record_start,
    )
    app.router.add_post(
        "/api/ptz/{cameraId}/patterns/{patternId}/record-stop",
        ptz_pattern_record_stop,
    )
