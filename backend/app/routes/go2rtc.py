"""
go2rtc Phase 1 routes — status, start, reverse proxy for dev UI.
"""

import asyncio
import logging

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from app.core.access_control import require_admin, require_user
from app.core.auth_context import get_effective_user
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_access import (
    is_admin,
    parse_stream_camera_id,
    user_can_access_stream,
)
from app.services.go2rtc_service import (
    ensure_go2rtc_streams,
    get_go2rtc_diagnostics,
    get_go2rtc_status,
    get_live_config,
    report_consumer,
    start_go2rtc,
    stop_go2rtc,
)
from app.services.go2rtc_workers import (
    get_api_url_for_camera_doc,
    get_api_url_for_stream,
    get_default_player_api_url,
    heal_all_workers,
    list_active_workers,
    rebalance_worker_assignments,
    sync_all_workers,
    sync_worker,
)
from app.services.stream_health import (
    clear_stale_stream_health_failures,
    ensure_stream_health_scan,
    start_stream_health_scan,
)

logger = logging.getLogger(__name__)

_BENIGN_WS_PHRASES = (
    "cannot write to closing transport",
    "connection closed",
    "connection reset",
    "closing transport",
    "broken pipe",
    "websocket connection is closed",
)

_GO2RTC_PLAYER_ASSETS = frozenset(
    {
        "video-stream.js",
        "video-rtc.js",
    }
)


def _benign_ws_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (asyncio.CancelledError, ConnectionResetError, ConnectionAbortedError)):
        return True
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _BENIGN_WS_PHRASES)


async def _admin_only(request: web.Request) -> web.Response | None:
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    return None


async def go2rtc_status(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await get_go2rtc_status())


async def go2rtc_diagnostics(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await get_go2rtc_diagnostics())


async def go2rtc_health_scan(request: web.Request) -> web.Response:
    """GET keeps/ensures a soft scan; POST forces a fresh scan."""
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    if request.method == "GET":
        return web.json_response({"ok": True, "healthScan": ensure_stream_health_scan()})
    return web.json_response({"ok": True, "healthScan": start_stream_health_scan(force=True)})


async def go2rtc_health_clear_failures(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    cleared = await clear_stale_stream_health_failures()
    return web.json_response({"ok": True, **cleared, "healthScan": start_stream_health_scan(force=True)})


async def go2rtc_reload(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await start_go2rtc(reload=True))


async def go2rtc_sync(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(await ensure_go2rtc_streams())


async def go2rtc_consumer(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"ok": False, "error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)
    stream = str(body.get("stream") or "").strip()
    delta = int(body.get("delta") or 0)
    if not stream or delta not in (-1, 1):
        return web.json_response({"ok": False, "error": "stream and delta required"}, status=400)

    if delta > 0 and not is_admin(user):
        stream_ref = parse_stream_camera_id(stream)
        if stream_ref:
            cam_doc = await get_camera_by_ref(stream_ref)
            if not user_can_access_stream(user, stream, cam_doc):
                return web.json_response({"ok": False, "error": "Camera access denied"}, status=403)

    report_consumer(stream, delta)
    return web.json_response({"ok": True})


async def go2rtc_client_error(request: web.Request) -> web.Response:
    """Record a final Live View playback failure into stream health / lastError."""
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"ok": False, "error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    camera_id = str(body.get("cameraId") or "").strip()
    camera_uid = str(body.get("cameraUid") or "").strip()
    stream = str(body.get("stream") or "").strip()
    message = str(body.get("message") or "").strip()[:500]
    if not message:
        return web.json_response({"ok": False, "error": "message required"}, status=400)

    ref = camera_id or camera_uid or parse_stream_camera_id(stream) or ""
    cam_doc = await get_camera_by_ref(ref) if ref else None
    if cam_doc is None and stream:
        cam_doc = await get_camera_by_ref(parse_stream_camera_id(stream) or "")
    if cam_doc is None:
        return web.json_response({"ok": False, "error": "Camera not found"}, status=404)

    if not is_admin(user) and not user_can_access_stream(user, stream or ref, cam_doc):
        return web.json_response({"ok": False, "error": "Camera access denied"}, status=403)

    from app.services.stream_health import record_stream_health
    from app.services.stream_issues import classify_stream_error

    result = record_stream_health(
        cam_doc,
        ok=False,
        message=message,
        category=classify_stream_error(message),
    )
    return web.json_response({"ok": True, "health": result})


async def go2rtc_client_ok(request: web.Request) -> web.Response:
    """Live View got video — mark camera Online in stream health."""
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"ok": False, "error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

    camera_id = str(body.get("cameraId") or "").strip()
    camera_uid = str(body.get("cameraUid") or "").strip()
    stream = str(body.get("stream") or "").strip()
    ref = camera_id or camera_uid or parse_stream_camera_id(stream) or ""
    cam_doc = await get_camera_by_ref(ref) if ref else None
    if cam_doc is None and stream:
        cam_doc = await get_camera_by_ref(parse_stream_camera_id(stream) or "")
    if cam_doc is None:
        return web.json_response({"ok": False, "error": "Camera not found"}, status=404)

    if not is_admin(user) and not user_can_access_stream(user, stream or ref, cam_doc):
        return web.json_response({"ok": False, "error": "Camera access denied"}, status=403)

    from app.services.stream_health import record_stream_health

    result = record_stream_health(cam_doc, ok=True, message="")
    return web.json_response({"ok": True, "health": result})


async def live_config(request: web.Request) -> web.Response:
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(get_live_config())


async def go2rtc_start(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await start_go2rtc())


async def go2rtc_stop(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    await stop_go2rtc()
    return web.json_response({"ok": True})


async def _authorize_go2rtc_proxy(request: web.Request, path: str) -> None:
    basename = path.rsplit("/", 1)[-1].lower()
    if basename in _GO2RTC_PLAYER_ASSETS or basename.endswith(".js"):
        return

    user = await get_effective_user(request)
    if user is None:
        raise web.HTTPUnauthorized(text="Authentication required")

    src = (request.query.get("src") or "").strip()
    if src:
        stream_ref = parse_stream_camera_id(src)
        if not stream_ref:
            raise web.HTTPForbidden(text="Invalid stream")
        cam_doc = await get_camera_by_ref(stream_ref)
        if cam_doc and cam_doc.get("is_active") is False:
            raise web.HTTPForbidden(text="Camera disabled")
        if not user_can_access_stream(user, src, cam_doc):
            raise web.HTTPForbidden(text="Camera access denied")
        return

    if is_admin(user):
        return

    path_lower = path.lower()
    if path_lower.startswith("api/streams") or path_lower.startswith("api/config"):
        raise web.HTTPForbidden(text="Admin only")

    raise web.HTTPForbidden(text="Camera access denied")


async def go2rtc_workers_list(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    workers = await list_active_workers()
    return web.json_response({"workers": workers})


async def go2rtc_worker_sync(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    try:
        worker_id = int(request.match_info.get("worker_id", "0"))
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid worker_id"}, status=400)
    if worker_id < 1:
        return web.json_response({"ok": False, "error": "invalid worker_id"}, status=400)
    return web.json_response(await sync_worker(worker_id))


async def go2rtc_workers_heal(request: web.Request) -> web.Response:
    """POST /api/go2rtc/workers/heal — restart/resync any unhealthy worker."""
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await heal_all_workers())


async def go2rtc_workers_rebalance(request: web.Request) -> web.Response:
    """POST /api/go2rtc/workers/rebalance — redistribute cameras across workers."""
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    rebalance = await rebalance_worker_assignments(reason="api")
    sync = await sync_all_workers()
    return web.json_response({"rebalance": rebalance, "sync": sync})


async def _resolve_go2rtc_api_url(
    request: web.Request,
    path: str,
    cam_doc: dict | None = None,
) -> str:
    basename = path.rsplit("/", 1)[-1].lower()
    if basename in _GO2RTC_PLAYER_ASSETS or basename.endswith(".js"):
        return await get_default_player_api_url()

    src = (request.query.get("src") or "").strip()
    if src:
        return await get_api_url_for_stream(src, cam_doc)
    if cam_doc is not None:
        return await get_api_url_for_camera_doc(cam_doc)
    return await get_default_player_api_url()


async def _proxy_to_go2rtc(
    request: web.Request,
    path: str,
    *,
    api_url: str,
) -> web.StreamResponse:
    """HTTP reverse proxy to a go2rtc worker API (single-origin browser access)."""
    target = f"{api_url.rstrip('/')}/{path}"
    if request.query_string:
        target = f"{target}?{request.query_string}"

    timeout = ClientTimeout(total=300)
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }

    async with ClientSession(timeout=timeout) as session:
        async with session.request(
            request.method,
            target,
            headers=headers,
            data=await request.read() if request.can_read_body else None,
            allow_redirects=False,
        ) as upstream:
            body = await upstream.read()
            resp = web.Response(body=body, status=upstream.status)
            for key, val in upstream.headers.items():
                if key.lower() in ("transfer-encoding", "connection", "content-encoding"):
                    continue
                resp.headers[key] = val
            return resp


async def go2rtc_proxy(request: web.Request) -> web.StreamResponse:
    path = request.match_info.get("path", "")
    await _authorize_go2rtc_proxy(request, path)

    cam_doc = None
    src = (request.query.get("src") or "").strip()
    if src:
        stream_ref = parse_stream_camera_id(src)
        if stream_ref:
            cam_doc = await get_camera_by_ref(stream_ref)

    api_url = await _resolve_go2rtc_api_url(request, path, cam_doc)
    return await _proxy_to_go2rtc(request, path, api_url=api_url)


async def go2rtc_ws_proxy(request: web.Request) -> web.WebSocketResponse:
    """WebSocket proxy for go2rtc /api/ws (WebRTC/MSE signaling)."""
    src = request.query.get("src", "")
    user = await get_effective_user(request)
    if user is None:
        raise web.HTTPUnauthorized(text="Authentication required")

    if not src:
        raise web.HTTPForbidden(text="src query parameter required")

    stream_ref = parse_stream_camera_id(src)
    if not stream_ref:
        raise web.HTTPForbidden(text="Invalid stream")
    cam_doc = await get_camera_by_ref(stream_ref)
    if cam_doc and cam_doc.get("is_active") is False:
        raise web.HTTPForbidden(text="Camera disabled")
    if not user_can_access_stream(user, src, cam_doc):
        raise web.HTTPForbidden(text="Camera access denied")

    api_url = await get_api_url_for_camera_doc(cam_doc)
    ws_base = api_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    target = (
        f"{ws_base}/api/ws?{request.query_string}"
        if request.query_string
        else f"{ws_base}/api/ws"
    )

    client_ws = web.WebSocketResponse()
    await client_ws.prepare(request)

    timeout = ClientTimeout(total=None)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(target) as upstream_ws:

                async def forward_to_client() -> None:
                    try:
                        async for msg in upstream_ws:
                            if client_ws.closed:
                                break
                            if msg.type == WSMsgType.TEXT:
                                await client_ws.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await client_ws.send_bytes(msg.data)
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                                break
                    except Exception as exc:
                        if not _benign_ws_disconnect(exc):
                            logger.warning(
                                "[go2rtc] WS client forward error src=%s: %s", src, exc
                            )

                async def forward_to_upstream() -> None:
                    try:
                        async for msg in client_ws:
                            if upstream_ws.closed:
                                break
                            if msg.type == WSMsgType.TEXT:
                                await upstream_ws.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await upstream_ws.send_bytes(msg.data)
                            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                                break
                    except Exception as exc:
                        if not _benign_ws_disconnect(exc):
                            logger.warning(
                                "[go2rtc] WS upstream forward error src=%s: %s", src, exc
                            )

                await asyncio.gather(
                    forward_to_client(),
                    forward_to_upstream(),
                    return_exceptions=True,
                )
    except Exception as exc:
        if not _benign_ws_disconnect(exc):
            logger.warning("[go2rtc] WS proxy error src=%s: %s", src, exc)
    finally:
        if not client_ws.closed:
            await client_ws.close()
    return client_ws


def setup_go2rtc_routes(app: web.Application) -> None:
    app.router.add_get("/api/go2rtc/live-config", live_config)
    app.router.add_get("/api/go2rtc/status", go2rtc_status)
    app.router.add_get("/api/go2rtc/diagnostics", go2rtc_diagnostics)
    app.router.add_get("/api/go2rtc/health-scan", go2rtc_health_scan)
    app.router.add_post("/api/go2rtc/health-scan", go2rtc_health_scan)
    app.router.add_post("/api/go2rtc/health-scan/clear-failures", go2rtc_health_clear_failures)
    app.router.add_post("/api/go2rtc/start", go2rtc_start)
    app.router.add_post("/api/go2rtc/stop", go2rtc_stop)
    app.router.add_post("/api/go2rtc/reload", go2rtc_reload)
    app.router.add_post("/api/go2rtc/sync", go2rtc_sync)
    app.router.add_get("/api/go2rtc/sync", go2rtc_sync)
    app.router.add_post("/api/go2rtc/consumer", go2rtc_consumer)
    app.router.add_post("/api/go2rtc/client-error", go2rtc_client_error)
    app.router.add_post("/api/go2rtc/client-ok", go2rtc_client_ok)
    app.router.add_get("/api/go2rtc/workers", go2rtc_workers_list)
    app.router.add_post("/api/go2rtc/workers/heal", go2rtc_workers_heal)
    app.router.add_post("/api/go2rtc/workers/rebalance", go2rtc_workers_rebalance)
    app.router.add_post("/api/go2rtc/workers/{worker_id}/sync", go2rtc_worker_sync)
    app.router.add_get("/go2rtc/api/ws", go2rtc_ws_proxy)
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        app.router.add_route(method, "/go2rtc/{path:.*}", go2rtc_proxy)
