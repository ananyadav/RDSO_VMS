"""RDSO 18.6.22.11 — Server↔Client encryption via reverse-proxy TLS (≥128-bit).

TLS terminates at Nginx (HTTPS/WSS). The aiohttp backend stays on localhost HTTP.
Local development HTTP remains allowed unless HTTPS_ENFORCE=1.
"""

from __future__ import annotations

import os
from typing import Any

from aiohttp import web

from app.services.client_media_routing import live_ws_path
from app.services.session_service import session_cookie_kwargs


def https_enforce_enabled() -> bool:
    return os.getenv("HTTPS_ENFORCE", "").strip().lower() in ("1", "true", "yes")


def request_is_secure(request: web.Request) -> bool:
    if request.secure:
        return True
    fwd = (request.headers.get("X-Forwarded-Proto") or "").strip().lower()
    return fwd == "https"


def _client_host(request: web.Request) -> str:
    peer = request.remote or ""
    return str(peer).split("%")[0]


def is_loopback_client(request: web.Request) -> bool:
    host = _client_host(request)
    return host in ("127.0.0.1", "::1", "localhost")


def allow_insecure_request(request: web.Request) -> bool:
    """Local HTTP allowed; production enforce rejects non-HTTPS remote clients."""
    if not https_enforce_enabled():
        return True
    if request_is_secure(request):
        return True
    # Health/readiness always reachable for local probes
    path = request.path or ""
    if path.startswith("/api/health") or path.startswith("/api/ready"):
        return True
    if is_loopback_client(request):
        return True
    return False


@web.middleware
async def https_enforce_middleware(request: web.Request, handler):
    if allow_insecure_request(request):
        return await handler(request)
    return web.json_response(
        {
            "error": "HTTPS required",
            "https_enforce": True,
            "hint": "Terminate TLS at Nginx and set X-Forwarded-Proto: https",
            "rdso_18_6_22_11": True,
        },
        status=403,
    )


def tls_capability_public(*, request: web.Request | None = None) -> dict[str, Any]:
    secure = request_is_secure(request) if request is not None else False
    cookie = (
        session_cookie_kwargs(request, max_age=3600)
        if request is not None
        else {"httponly": True, "secure": False, "samesite": "Lax"}
    )
    return {
        "rdso_18_6_22_11": True,
        "min_symmetric_bits": 128,
        "preferred_tls_versions": ["TLSv1.2", "TLSv1.3"],
        "architecture": "Client → HTTPS/WSS Nginx → localhost backend/go2rtc",
        "tls_terminated_at": "nginx_reverse_proxy",
        "aiohttp_terminates_tls": False,
        "https_enforce_env": "HTTPS_ENFORCE",
        "https_enforce_enabled": https_enforce_enabled(),
        "request_secure": secure,
        "session_cookie": {
            "httponly": bool(cookie.get("httponly")),
            "samesite": cookie.get("samesite"),
            "secure": bool(cookie.get("secure")),
            "secure_when_https_or_SESSION_COOKIE_SECURE": True,
        },
        "media_routing": {
            "relative_urls_only": True,
            "example_ws_path": live_ws_path(1),
            "browser_upgrades_to_wss_on_https_page": True,
            "mixed_content_avoided": True,
            "never_returns_camera_rtsp_or_credentials": True,
        },
        "deploy_sample": "deploy/nginx-cctv-tls.sample.conf",
        "local_http_allowed_when_enforce_off": True,
    }


async def ccc_security_endpoint(request: web.Request) -> web.Response:
    return web.json_response(tls_capability_public(request=request))
