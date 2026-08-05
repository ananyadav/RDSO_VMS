"""Resolve logged-in user from HttpOnly session cookie (legacy X-User-Id fallback)."""

from __future__ import annotations

from typing import Optional, Tuple

from aiohttp import web
from bson.objectid import ObjectId

from app.core.database import user_collection
from app.services.session_service import clear_session_cookie, read_session_token, resolve_session_user


def _read_legacy_user_id(request) -> str:
    uid = (request.headers.get("X-User-Id") or "").strip()
    if not uid:
        uid = (request.query.get("uid") or request.query.get("userId") or "").strip()
    return uid


async def resolve_auth(request) -> Tuple[Optional[dict], bool, bool]:
    """
    Resolve the caller's user document.

    Returns (user, invalid, stale_cookie):
    - No credentials → (None, False, False)
    - Valid session / legacy id → (user_doc, False, False)
    - Stale session cookie → (None, False, True) — clear cookie, allow login
    - Legacy id for deleted user → (None, True, False)
    """
    user, stale_cookie = await resolve_session_user(request)
    if user is not None:
        return user, False, False

    uid = _read_legacy_user_id(request)
    if uid:
        try:
            legacy_user = await user_collection.find_one({"_id": ObjectId(uid)})
            if legacy_user:
                return legacy_user, False, stale_cookie
            return None, True, stale_cookie
        except Exception:
            return None, True, stale_cookie

    return None, False, stale_cookie


async def get_user_from_request(request) -> Optional[dict]:
    """Load user from session cookie or legacy header/query."""
    user, invalid, _stale = await resolve_auth(request)
    if invalid:
        return None
    return user


async def get_effective_user(request) -> Optional[dict]:
    """
    User for access checks. Uses middleware cache when available.

    Returns None when unauthenticated — protected routes reject in session_middleware.
    """
    if "auth_user" in request:
        return request["auth_user"]
    user, invalid, _stale = await resolve_auth(request)
    if invalid:
        raise web.HTTPUnauthorized(
            text="Session invalid — user was deleted or access was revoked.",
            content_type="text/plain",
        )
    return user


_PUBLIC_API_PREFIXES = (
    "/api/health",
    "/api/login",
    "/api/logout",
)

# go2rtc player bundle — loaded via <script type="module">; cannot send custom headers
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
    if path.startswith("/go2rtc/"):
        return True
    if path.startswith("/media/"):
        return True
    return False


@web.middleware
async def session_middleware(request: web.Request, handler):
    """Reject stale sessions and require login for API / go2rtc / WebSocket routes."""
    path = request.path or ""
    had_cookie = bool(read_session_token(request))

    user, invalid, stale_cookie = await resolve_auth(request)
    if invalid:
        response = web.json_response(
            {
                "error": (
                    "Session invalid — user was deleted or access was revoked. "
                    "Please log in again."
                )
            },
            status=401,
        )
        if stale_cookie or had_cookie:
            clear_session_cookie(response, request)
        return response

    if _requires_authentication(path) and user is None:
        response = web.json_response({"error": "Authentication required"}, status=401)
        if stale_cookie or (had_cookie and not user):
            clear_session_cookie(response, request)
        return response

    request["auth_user"] = user
    response = await handler(request)
    if stale_cookie or (had_cookie and user is None):
        clear_session_cookie(response, request)
    return response
