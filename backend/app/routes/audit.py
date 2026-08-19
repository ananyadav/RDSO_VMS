"""SUPER_ADMIN-only audit log query. Append-only: no DELETE."""

from aiohttp import web

from app.core.access_control import deny_unless_super_admin
from app.services.audit_service import query_audit_logs


def _bool_arg(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


async def list_audit_logs_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    data = await query_audit_logs(
        actor_user_id=q.get("user") or q.get("actor_user_id"),
        actor_role=q.get("role") or q.get("actor_role"),
        action=q.get("action"),
        resource_type=q.get("resource_type"),
        resource_id=q.get("resource_id"),
        start=q.get("start") or q.get("from"),
        end=q.get("end") or q.get("to"),
        success=_bool_arg(q.get("success")),
        limit=limit,
        offset=offset,
    )
    return web.json_response(data)


def setup_audit_routes(app: web.Application) -> None:
    app.router.add_get("/api/audit-logs", list_audit_logs_endpoint)
