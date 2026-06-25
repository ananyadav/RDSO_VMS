"""Authentication and admin authorization helpers."""

from __future__ import annotations

from typing import Optional

from aiohttp import web

from app.core.auth_context import get_effective_user
from app.services.camera_access import is_admin
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_access import user_can_access_camera

PERMISSION_PLAYBACK = "Playback"
PERMISSION_LIVE_VIEW = "Live View"


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def has_permission(user: Optional[dict], permission: str) -> bool:
    if not user:
        return False
    if is_admin(user):
        return True
    perms = user.get("permissions") or []
    return permission in perms


async def require_user(request: web.Request) -> dict:
    user = await get_effective_user(request)
    if user is None:
        raise web.HTTPUnauthorized(text="Authentication required")
    return user


async def require_admin(request: web.Request) -> dict:
    user = await require_user(request)
    if not is_admin(user):
        raise web.HTTPForbidden(text="Admin only")
    return user


async def deny_unless_playback_permission(request: web.Request) -> web.Response | None:
    """Return 401/403 when the caller may not use recorded playback APIs."""
    user = await get_effective_user(request)
    if user is None:
        return _json_error("Authentication required", 401)
    if is_admin(user):
        return None
    if not has_permission(user, PERMISSION_PLAYBACK):
        return _json_error("Playback permission required", 403)
    return None


async def deny_unless_camera_access(
    request: web.Request,
    camera_ref: str,
    *,
    camera_doc: dict | None = None,
) -> web.Response | None:
    """Return a 401/403/404 response when the caller may not access camera_ref."""
    user = await get_effective_user(request)
    if user is None:
        return _json_error("Authentication required", 401)
    if is_admin(user):
        return None

    cam = camera_doc
    if cam is None:
        cam = await get_camera_by_ref(camera_ref)
    if not cam:
        return _json_error("Camera not found", 404)
    if not user_can_access_camera(user, camera_ref, cam):
        return _json_error("Camera access denied", 403)
    return None
