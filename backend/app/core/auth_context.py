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

    Returns None when no header — callers treat None as full access for
    backward compatibility with unauthenticated API clients.
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


@web.middleware
async def session_middleware(request: web.Request, handler):
    """Reject stale client sessions when X-User-Id / uid refers to a deleted user."""
    path = request.path or ""
    if path.startswith("/api/login"):
        return await handler(request)

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

    request["auth_user"] = user
    return await handler(request)
