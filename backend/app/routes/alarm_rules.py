"""Alarm rule CRUD — ADMIN write, Events-permission read."""

from aiohttp import web

from app.core.access_control import (
    deny_unless_admin,
    deny_unless_admin_or_events_read,
)
from app.core.auth_context import get_effective_user
from app.core.http_utils import read_json_body
from app.services.alarm_rule_service import (
    AlarmRuleValidationError,
    create_alarm_rule,
    delete_alarm_rule,
    get_alarm_rule,
    get_alarm_rule_doc,
    list_alarm_rules,
    rule_to_public,
    update_alarm_rule,
)
from app.services.audit_service import (
    ACTION_ALARM_RULE_CREATED,
    ACTION_ALARM_RULE_DELETED,
    ACTION_ALARM_RULE_UPDATED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
    field_diff,
)


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


async def list_alarm_rules_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin_or_events_read(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 100)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    data = await list_alarm_rules(
        camera_id=q.get("camera_id"),
        enabled=_bool_query(q.get("enabled")),
        source_type=q.get("source_type"),
        limit=limit,
        offset=offset,
    )
    return web.json_response(data)


async def get_alarm_rule_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin_or_events_read(request)
    if denied is not None:
        return denied
    rule_id = request.match_info.get("id")
    rule = await get_alarm_rule(rule_id or "")
    if not rule:
        return web.json_response({"error": "Alarm rule not found"}, status=404)
    return web.json_response(rule)


async def create_alarm_rule_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    payload, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    try:
        created = await create_alarm_rule(payload, created_by=str(actor.get("_id") or actor.get("id") or ""))
    except AlarmRuleValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    async def _compensate():
        await delete_alarm_rule(created["id"])

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_ALARM_RULE_CREATED,
        actor=actor,
        resource_type="alarm_rule",
        resource_id=created["id"],
        resource_label=created.get("name"),
        request=request,
        success=True,
        metadata={
            "camera_id": created.get("camera_id"),
            "source_type": (created.get("trigger") or {}).get("source_type"),
            "severity": created.get("severity"),
        },
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(created, status=201)


async def update_alarm_rule_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    rule_id = request.match_info.get("id") or ""
    before_doc = await get_alarm_rule_doc(rule_id)
    if not before_doc:
        return web.json_response({"error": "Alarm rule not found"}, status=404)

    payload, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    try:
        updated = await update_alarm_rule(rule_id, payload)
    except AlarmRuleValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not updated:
        return web.json_response({"error": "Alarm rule not found"}, status=404)

    before_public = rule_to_public(before_doc)
    changes = field_diff(before_public, updated, list(payload.keys()))

    async def _compensate():
        restore = {k: before_doc.get(k) for k in before_doc if k != "_id"}
        from app.core.database import alarm_rules_collection
        from bson import ObjectId

        await alarm_rules_collection.replace_one({"_id": ObjectId(rule_id)}, before_doc)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_ALARM_RULE_UPDATED,
        actor=actor,
        resource_type="alarm_rule",
        resource_id=rule_id,
        resource_label=updated.get("name"),
        request=request,
        success=True,
        changes=changes,
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(updated)


async def delete_alarm_rule_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    rule_id = request.match_info.get("id") or ""
    before_doc = await get_alarm_rule_doc(rule_id)
    if not before_doc:
        return web.json_response({"error": "Alarm rule not found"}, status=404)

    deleted = await delete_alarm_rule(rule_id)
    if not deleted:
        return web.json_response({"error": "Alarm rule not found"}, status=404)

    async def _compensate():
        from app.core.database import alarm_rules_collection

        await alarm_rules_collection.replace_one({"_id": before_doc["_id"]}, before_doc, upsert=True)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_ALARM_RULE_DELETED,
        actor=actor,
        resource_type="alarm_rule",
        resource_id=rule_id,
        resource_label=before_doc.get("name"),
        request=request,
        success=True,
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response({}, status=204)


def setup_alarm_rule_routes(app: web.Application) -> None:
    app.router.add_get("/api/alarm-rules", list_alarm_rules_endpoint)
    app.router.add_post("/api/alarm-rules", create_alarm_rule_endpoint)
    app.router.add_get("/api/alarm-rules/{id}", get_alarm_rule_endpoint)
    app.router.add_put("/api/alarm-rules/{id}", update_alarm_rule_endpoint)
    app.router.add_delete("/api/alarm-rules/{id}", delete_alarm_rule_endpoint)
