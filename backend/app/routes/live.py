"""
Live HLS streaming routes
"""

import logging
from pathlib import Path
from aiohttp import web

from app.core.auth_context import get_effective_user
from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.video_live_hls import (
    subscribe,
    unsubscribe,
    batch_subscribe,
    is_playlist_ready,
    get_stream_status,
    get_live_diagnostics,
    LIVE_DIR,
)
from app.services.ffmpeg_orphan_cleanup import (
    cleanup_orphan_ffmpeg,
    get_orphans_report,
)
from app.services.live_stream_debug import get_fullscreen_debug
from app.services.live_latency import ingest_telemetry_payload


def _base_camera_id(camera_id: str) -> str:
    if camera_id.endswith("__fullscreen"):
        return camera_id.replace("__fullscreen", "")
    return camera_id


async def _deny_unless_allowed(request: web.Request, camera_id: str) -> web.Response | None:
    from app.core.access_control import deny_unless_camera_access

    return await deny_unless_camera_access(request, _base_camera_id(camera_id))


async def _filter_allowed_camera_ids(request: web.Request, camera_ids: list[str]) -> list[str]:
    user = await get_effective_user(request)
    if user is None:
        return []
    if is_admin(user):
        return camera_ids
    allowed: list[str] = []
    for cid in camera_ids:
        base_id = _base_camera_id(str(cid))
        cam = await get_camera_by_ref(base_id)
        if cam and user_can_access_camera(user, base_id, cam):
            allowed.append(str(cid))
    return allowed


async def live_start(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    denied = await _deny_unless_allowed(request, camera_id)
    if denied is not None:
        return denied
    force_sub = request.query.get("forceSub") in ("1", "true", "yes")
    if not force_sub:
        try:
            body = await request.json()
            force_sub = bool(body.get("forceSub"))
        except Exception:
            pass
    result = await subscribe(camera_id, wait_ready=False, force_sub=force_sub)
    if not result.ok:
        return web.json_response(
            {"error": result.error or "Failed to start stream"},
            status=500,
        )
    return web.json_response({
        "status": "ok",
        "reused": result.reused,
        "playlist": f"/api/live/{camera_id}/live.m3u8",
        **(await get_stream_status(camera_id)),
    })


async def live_batch_start(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    camera_ids = body.get("cameraIds") or []
    if not isinstance(camera_ids, list):
        return web.json_response({"error": "cameraIds must be a list"}, status=400)
    camera_ids = await _filter_allowed_camera_ids(request, [str(c) for c in camera_ids])
    profile = body.get("profile") or "grid"
    result = await batch_subscribe([str(c) for c in camera_ids], profile=str(profile))
    return web.json_response({"status": "ok", **result})


async def live_ready(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    denied = await _deny_unless_allowed(request, camera_id)
    if denied is not None:
        return denied
    return web.json_response({"ready": await is_playlist_ready(camera_id)})


async def live_status(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    denied = await _deny_unless_allowed(request, camera_id)
    if denied is not None:
        return denied
    return web.json_response(await get_stream_status(camera_id))


async def live_diagnostics(_request: web.Request) -> web.Response:
    return web.json_response(await get_live_diagnostics())


async def live_orphans(_request: web.Request) -> web.Response:
    """GET /api/live/orphans — tracked vs orphan NVR FFmpeg processes."""
    return web.json_response(get_orphans_report())


async def live_orphans_cleanup(_request: web.Request) -> web.Response:
    """POST /api/live/orphans/cleanup — kill orphan NVR FFmpeg only."""
    result = await cleanup_orphan_ffmpeg()
    return web.json_response({"status": "ok", **result})


async def live_telemetry(request: web.Request) -> web.Response:
    """POST /api/live/telemetry — browser HLS latency snapshots (diagnostics only)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "error": "Body must be object"}, status=400)
    return web.json_response(ingest_telemetry_payload(body))


async def live_debug(request: web.Request) -> web.Response:
    """GET /api/live/debug/{cameraId} — last fullscreen ffprobe snapshot."""
    camera_id = request.match_info["cameraId"]
    if camera_id.endswith("__fullscreen"):
        camera_id = camera_id.replace("__fullscreen", "")
    run_probe = request.query.get("probe") in ("1", "true", "yes")
    if run_probe:
        from app.services.live_stream_debug import schedule_probe_when_playlist_ready

        schedule_probe_when_playlist_ready(f"{camera_id}__fullscreen")
    data = await get_fullscreen_debug(camera_id)
    if data is None:
        return web.json_response(
            {"error": "No fullscreen verification data for this camera yet"},
            status=404,
        )
    return web.json_response(data)


async def live_stop(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    denied = await _deny_unless_allowed(request, camera_id)
    if denied is not None:
        return denied
    await unsubscribe(camera_id)
    return web.json_response({"status": "ok"})


async def live_file(request: web.Request) -> web.Response:
    camera_id = request.match_info["cameraId"]
    denied = await _deny_unless_allowed(request, camera_id)
    if denied is not None:
        return denied
    filename = request.match_info["filename"]

    base_dir = (LIVE_DIR / camera_id).resolve()
    file_path = (base_dir / filename).resolve()
    if not str(file_path).startswith(str(base_dir)):
        return web.Response(status=403, text="Forbidden")

    if not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="Not found")

    suffix = file_path.suffix.lower()
    if suffix == ".m3u8":
        content_type = "application/vnd.apple.mpegurl"
    elif suffix == ".ts":
        content_type = "video/mp2t"
    else:
        content_type = "application/octet-stream"

    return web.FileResponse(
        file_path,
        headers={
            "Content-Type": content_type,
            "Cache-Control": "no-cache, no-store",
            "Access-Control-Allow-Origin": "*",
        },
    )


def setup_live_routes(app: web.Application):
    app.router.add_post("/api/live/batch-start", live_batch_start)
    app.router.add_get("/api/live/diagnostics", live_diagnostics)
    app.router.add_post("/api/live/telemetry", live_telemetry)
    app.router.add_get("/api/live/orphans", live_orphans)
    app.router.add_post("/api/live/orphans/cleanup", live_orphans_cleanup)
    app.router.add_get("/api/live/debug/{cameraId}", live_debug)
    app.router.add_post("/api/live/{cameraId}/start", live_start)
    app.router.add_get("/api/live/{cameraId}/ready", live_ready)
    app.router.add_get("/api/live/{cameraId}/status", live_status)
    app.router.add_post("/api/live/{cameraId}/stop", live_stop)
    app.router.add_get("/api/live/{cameraId}/{filename}", live_file)

    async def on_cleanup(_app):
        from app.services.video_live_hls import cleanup_all
        from app.services.ffmpeg_orphan_cleanup import shutdown_all_nvr_ffmpeg

        await cleanup_all()
        await shutdown_all_nvr_ffmpeg()

    app.on_cleanup.append(on_cleanup)
