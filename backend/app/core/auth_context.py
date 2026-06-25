"""Resolve logged-in user from request headers."""

from __future__ import annotations

from typing import Optional, Tuple

from aiohttp import web
from bson.objectid import ObjectId

from app.core.database import user_collection


def _read_user_id(request) -> str:
    uid = (request.headers.get("X-User-Id") or "").strip()
    if not uid:
        uid = (request.query.get("uid") or request.query.get("userId") or "").strip()
    return uid


async def resolve_auth(request) -> Tuple[Optional[dict], bool]:
    """
    Resolve the caller's user document.

    Returns (user, invalid):
    - No user id header/param → (None, False) — legacy anonymous access
    - Valid id → (user_doc, False)
    - Id present but user missing/invalid → (None, True) — deleted or revoked session
    """
    uid = _read_user_id(request)
    if not uid:
        return None, False
    try:
        user = await user_collection.find_one({"_id": ObjectId(uid)})
        if user:
            return user, False
        return None, True
    except Exception:
        return None, True


async def get_user_from_request(request) -> Optional[dict]:
    """Read X-User-Id header or uid/userId query param (WebSocket) and load user."""
    user, invalid = await resolve_auth(request)
    if invalid:
        return None
    return user


async def get_effective_user(request) -> Optional[dict]:
    """
    User for access checks. Uses middleware cache when available.

    Returns None when no X-User-Id / uid header — protected routes reject
    unauthenticated callers in session_middleware.
    """
    if "auth_user" in request:
        return request["auth_user"]
    user, invalid = await resolve_auth(request)
    if invalid:
        raise web.HTTPUnauthorized(
            text="Session invalid — user was deleted or access was revoked.",
            content_type="text/plain",
        )
    return user


_PUBLIC_API_PREFIXES = (
    "/api/login",
)

# go2rtc player bundle — loaded via <script type="module">; cannot send X-User-Id header
_PUBLIC_GO2RTC_PATHS = frozenset(
    {
        "/go2rtc/video-stream.js",
        "/go2rtc/video-rtc.js",
    }
)


def _is_public_go2rtc_asset(path: str) -> bool:
    if path in _PUBLIC_GO2RTC_PATHS:
        return True
    # ES module graph (video-stream.js imports ./video-rtc.js, etc.)
    if path.startswith("/go2rtc/") and path.rsplit("/", 1)[-1].endswith(".js"):
        return True
    return False


def _requires_authentication(path: str) -> bool:
    if _is_public_go2rtc_asset(path):
        return False
    if path.startswith("/api/"):
        return not any(path.startswith(prefix) for prefix in _PUBLIC_API_PREFIXES)
    if path.startswith("/go2rtc/") or path == "/ws":
        return True
    return False


@web.middleware
async def session_middleware(request: web.Request, handler):
    """Reject stale sessions and require login for API / go2rtc / WebSocket routes."""
    path = request.path or ""

    user, invalid = await resolve_auth(request)
    if invalid:
        return web.json_response(
            {
                "error": (
                    "Session invalid — user was deleted or access was revoked. "
                    "Please log in again."
                )
            },
            status=401,
        )

    if _requires_authentication(path) and user is None:
        return web.json_response({"error": "Authentication required"}, status=401)

    request["auth_user"] = user
    return await handler(request)
