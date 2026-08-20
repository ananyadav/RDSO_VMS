"""Authentication and admin authorization helpers."""

from __future__ import annotations

from typing import Optional

from aiohttp import web

from app.core.auth_context import get_effective_user
from app.core.roles import ROLE_SUPER_ADMIN, ROLE_VIEWER, is_operator, normalize_role
from app.services.camera_access import is_admin
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_access import user_can_access_camera

PERMISSION_PLAYBACK = "Playback"  # legacy operator assignment; not used for recording APIs
PERMISSION_RECORDING_VIEW = "recording.view"
PERMISSION_LIVE_VIEW = "Live View"
PERMISSION_SYSTEM = "System"


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def has_live_view(user: Optional[dict]) -> bool:
    """Admin and Super Admin always; Operator/Viewer always (camera ACL still applies)."""
    if not user:
        return False
    if is_admin(user):
        return True
    if is_operator(user) or normalize_role(user) == ROLE_VIEWER:
        return True
    perms = user.get("permissions") or []
    return PERMISSION_LIVE_VIEW in perms


def has_permission(user: Optional[dict], permission: str) -> bool:
    if not user:
        return False
    if is_admin(user):
        return True
    if permission == PERMISSION_LIVE_VIEW:
        return has_live_view(user)
    perms = user.get("permissions") or []
    return permission in perms


def has_recording_view(user: Optional[dict]) -> bool:
    """SUPER_ADMIN and ADMIN always; others only with explicit recording.view."""
    if not user:
        return False
    if is_admin(user):
        return True
    perms = user.get("permissions") or []
    return PERMISSION_RECORDING_VIEW in perms


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


def user_has_role(user: Optional[dict], *roles: str) -> bool:
    current = normalize_role(user)
    if not current:
        return False
    wanted = {normalize_role(role) for role in roles if role}
    return current in wanted


async def require_role(request: web.Request, *roles: str) -> dict:
    """Fail closed: unauthenticated → 401, wrong role → 403."""
    user = await require_user(request)
    if not user_has_role(user, *roles):
        raise web.HTTPForbidden(text="Forbidden")
    return user


async def require_super_admin(request: web.Request) -> dict:
    return await require_role(request, ROLE_SUPER_ADMIN)


async def deny_unless_super_admin(request: web.Request) -> web.Response | None:
    try:
        await require_super_admin(request)
    except web.HTTPUnauthorized:
        return _json_error("Authentication required", 401)
    except web.HTTPForbidden:
        return _json_error("Forbidden", 403)
    return None


async def deny_unless_playback_permission(request: web.Request) -> web.Response | None:
    """Return 401/403 when the caller may not view/search/play recordings.

    SUPER_ADMIN and ADMIN are allowed. OPERATOR/Viewer need recording.view.
    Legacy 'Playback' is not treated as recording.view.
    """
    user = await get_effective_user(request)
    if user is None:
        return _json_error("Authentication required", 401)
    if not has_recording_view(user):
        return _json_error("Recording view permission required", 403)
    return None


async def deny_unless_admin(request: web.Request) -> web.Response | None:
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return _json_error("Authentication required", 401)
    except web.HTTPForbidden:
        return _json_error("Admin only", 403)
    return None


async def deny_unless_admin_or_system(request: web.Request) -> web.Response | None:
    """Admin or users with System permission (Storage / recording management UI)."""
    user = await get_effective_user(request)
    if user is None:
        return _json_error("Authentication required", 401)
    if is_admin(user):
        return None
    if not has_permission(user, PERMISSION_SYSTEM):
        return _json_error("System permission required", 403)
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
