"""SUPER_ADMIN session oversight. Tokens are never returned."""

from aiohttp import web

from app.core.access_control import deny_unless_super_admin
from app.core.database import get_user_by_id
from app.core.http_utils import read_json_body
from app.services.audit_service import ACTION_SESSION_REVOKED, AUDIT_INCOMPLETE_ERROR, commit_critical_audit
from app.services.session_service import list_sessions, restore_sessions, revoke_sessions_for_user_tracked
from app.services.user_rbac import GENERIC_FORBIDDEN


async def list_sessions_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    active = (q.get("active") or "").strip().lower() in ("1", "true", "yes")
    data = await list_sessions(
        user_id=q.get("user_id") or q.get("user"),
        active_only=active,
        limit=limit,
        offset=offset,
    )
    return web.json_response(data)


async def revoke_sessions_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    body, err = await read_json_body(request)
    if err is not None:
        return err
    target_id = (body.get("user_id") or body.get("userId") or "").strip()
    if not target_id:
        return web.json_response({"error": "user_id required"}, status=400)
    target = await get_user_by_id(target_id)
    if not target:
        return web.json_response({"error": GENERIC_FORBIDDEN}, status=403)
    actor = request.get("auth_user")
    count, session_ids = await revoke_sessions_for_user_tracked(
        target_id, revoked_by=str(actor.get("_id")) if actor else None
    )

    async def _compensate():
        await restore_sessions(session_ids)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_SESSION_REVOKED,
        actor=actor,
        resource_type="user",
        resource_id=target_id,
        resource_label=(target.get("username") or target.get("name") or ""),
        request=request,
        success=True,
        metadata={"revoked_count": count},
    )
    if not ok:
        return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)
    return web.json_response({"ok": True, "revoked": count})


def setup_session_routes(app: web.Application) -> None:
    app.router.add_get("/api/sessions", list_sessions_endpoint)
    app.router.add_post("/api/sessions/revoke", revoke_sessions_endpoint)
