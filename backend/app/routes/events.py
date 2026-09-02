"""Event query and acknowledgement — Events permission + camera ACL."""

from aiohttp import web

from app.core.access_control import deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.audit_service import (
    ACTION_EVENT_ACKNOWLEDGED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
)
from app.services.event_service import acknowledge_event, get_event, list_events


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


def _audit_incomplete() -> web.Response:
    return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)


async def list_events_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    data = await list_events(
        user,
        camera_id=q.get("camera_id"),
        source_type=q.get("source_type"),
        severity=q.get("severity"),
        status=q.get("status"),
        acknowledged=_bool_query(q.get("acknowledged")),
        ui_notification=_bool_query(q.get("ui_notification")),
        from_ts=q.get("from"),
        to_ts=q.get("to"),
        limit=limit,
        offset=offset,
    )
    return web.json_response(data)


async def get_event_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    event_id = request.match_info.get("id") or ""
    event = await get_event(event_id, user)
    if not event:
        return web.json_response({"error": "Event not found"}, status=404)
    return web.json_response(event)


async def acknowledge_event_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    event_id = request.match_info.get("id") or ""

    before = await get_event(event_id, user)
    if not before:
        return web.json_response({"error": "Event not found"}, status=404)

    updated = await acknowledge_event(event_id, user)
    if not updated:
        return web.json_response({"error": "Event not found"}, status=404)

    async def _compensate():
        from app.core.database import events_collection
        from bson import ObjectId

        await events_collection.update_one(
            {"_id": ObjectId(event_id)},
            {
                "$set": {
                    "acknowledged": before.get("acknowledged", False),
                    "acknowledged_by": before.get("acknowledged_by"),
                    "acknowledged_at": before.get("acknowledged_at"),
                    "status": before.get("status", "open"),
                }
            },
        )

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_EVENT_ACKNOWLEDGED,
        actor=user,
        resource_type="event",
        resource_id=event_id,
        resource_label=updated.get("title"),
        request=request,
        success=True,
        metadata={
            "camera_id": updated.get("camera_id"),
            "source_type": updated.get("source_type"),
            "severity": updated.get("severity"),
        },
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(updated)


def setup_event_routes(app: web.Application) -> None:
    app.router.add_get("/api/events", list_events_endpoint)
    app.router.add_get("/api/events/{id}", get_event_endpoint)
    app.router.add_post("/api/events/{id}/acknowledge", acknowledge_event_endpoint)
