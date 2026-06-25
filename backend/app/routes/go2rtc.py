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
    GO2RTC_API_URL,
    ensure_go2rtc_streams,
    get_go2rtc_diagnostics,
    get_go2rtc_status,
    get_live_config,
    report_consumer,
    start_go2rtc,
    stop_go2rtc,
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


async def go2rtc_reload(request: web.Request) -> web.Response:
    denied = await _admin_only(request)
    if denied is not None:
        return denied
    return web.json_response(await start_go2rtc(reload=True))


async def go2rtc_sync(request: web.Request) -> web.Response:
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


async def _proxy_to_go2rtc(request: web.Request, path: str) -> web.StreamResponse:
    """HTTP reverse proxy to go2rtc API (single-origin browser access)."""
    await _authorize_go2rtc_proxy(request, path)

    target = f"{GO2RTC_API_URL}/{path}"
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
    return await _proxy_to_go2rtc(request, path)


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

    ws_base = GO2RTC_API_URL.replace("http://", "ws://", 1)
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
    app.router.add_get("/api/live/config", live_config)
    app.router.add_get("/api/go2rtc/status", go2rtc_status)
    app.router.add_get("/api/go2rtc/diagnostics", go2rtc_diagnostics)
    app.router.add_post("/api/go2rtc/start", go2rtc_start)
    app.router.add_post("/api/go2rtc/stop", go2rtc_stop)
    app.router.add_post("/api/go2rtc/reload", go2rtc_reload)
    app.router.add_post("/api/go2rtc/sync", go2rtc_sync)
    app.router.add_get("/api/go2rtc/sync", go2rtc_sync)
    app.router.add_post("/api/go2rtc/consumer", go2rtc_consumer)
    app.router.add_get("/go2rtc/api/ws", go2rtc_ws_proxy)
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        app.router.add_route(method, "/go2rtc/{path:.*}", go2rtc_proxy)
