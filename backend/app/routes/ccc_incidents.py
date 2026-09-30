"""RDSO 18.6 — CCC incident / event-log / SOP / escalation routes."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_events_permission, require_user
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_incident_escalation import (
    assign_incident_responder,
    process_due_escalations,
)
from app.services.ccc_incident_service import (
    IncidentDuplicateError,
    IncidentPermissionError,
    IncidentValidationError,
    create_incident,
    get_incident,
    list_event_log,
    list_incidents,
    promote_event_to_incident,
    update_incident,
)
from app.services.ccc_sop_service import (
    SopValidationError,
    create_sop_workflow,
    delete_sop_workflow,
    get_sop_workflow,
    list_sop_workflows,
    update_sop_workflow,
)

logger = logging.getLogger(__name__)

ACTION_CCC_INCIDENT_CREATED = "CCC_INCIDENT_CREATED"
ACTION_CCC_INCIDENT_UPDATED = "CCC_INCIDENT_UPDATED"
ACTION_CCC_INCIDENT_ASSIGNED = "CCC_INCIDENT_ASSIGNED"
ACTION_CCC_INCIDENT_ESCALATED = "CCC_INCIDENT_ESCALATED"
ACTION_CCC_SOP_CREATED = "CCC_SOP_CREATED"
ACTION_CCC_SOP_UPDATED = "CCC_SOP_UPDATED"
ACTION_CCC_SOP_DELETED = "CCC_SOP_DELETED"


def _bool_q(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes")


async def ccc_event_log_endpoint(request: web.Request) -> web.Response:
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
    try:
        data = await list_event_log(
            user,
            limit=limit,
            offset=offset,
            severity=q.get("severity"),
            status=q.get("incident_status") or q.get("status"),
            critical_only=_bool_q(q.get("critical_only")),
            q=(q.get("q") or "").strip(),
        )
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    return web.json_response(data)


async def ccc_incidents_list_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
        priority_min = int(q["priority_min"]) if q.get("priority_min") not in (None, "") else None
    except ValueError:
        return web.json_response({"error": "Invalid pagination/priority"}, status=400)
    try:
        data = await list_incidents(
            user,
            status=q.get("status"),
            severity=q.get("severity"),
            priority_min=priority_min,
            critical_only=_bool_q(q.get("critical_only")),
            assignee_group=q.get("assignee_group"),
            assignee_user_id=q.get("assignee_user_id"),
            location=q.get("location"),
            q=(q.get("q") or "").strip(),
            limit=limit,
            offset=offset,
        )
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    return web.json_response(data)


async def ccc_incident_get_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        inc = await get_incident(request.match_info.get("id") or "", user)
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    if not inc:
        return web.json_response({"error": "Incident not found"}, status=404)
    return web.json_response(inc)


async def ccc_incident_create_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    try:
        if body.get("event_id") or body.get("from_event_id"):
            eid = str(body.get("event_id") or body.get("from_event_id"))
            inc = await promote_event_to_incident(user, eid, **{
                k: body[k]
                for k in (
                    "title",
                    "location",
                    "status",
                    "incident_time",
                    "severity",
                    "priority",
                    "assignee_user_id",
                    "assignee_user_name",
                    "assignee_group",
                    "linked_camera_ids",
                    "notes",
                    "sop_workflow_id",
                )
                if k in body
            })
        else:
            inc = await create_incident(
                user,
                title=str(body.get("title") or ""),
                location=str(body.get("location") or ""),
                status=str(body.get("status") or "open"),
                incident_time=body.get("incident_time"),
                severity=str(body.get("severity") or "warning"),
                priority=body.get("priority"),
                assignee_user_id=body.get("assignee_user_id"),
                assignee_user_name=body.get("assignee_user_name"),
                assignee_group=body.get("assignee_group"),
                linked_event_ids=body.get("linked_event_ids"),
                linked_camera_ids=body.get("linked_camera_ids"),
                notes=body.get("notes"),
                sop_workflow_id=body.get("sop_workflow_id"),
                source="manual",
            )
    except IncidentDuplicateError as exc:
        return web.json_response({"error": str(exc), "duplicate": True}, status=409)
    except IncidentValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    except Exception as exc:
        logger.exception("[ccc] create incident")
        return web.json_response({"error": str(exc)}, status=500)

    await write_audit(
        action=ACTION_CCC_INCIDENT_CREATED,
        actor=user,
        resource_type="ccc_incident",
        resource_id=inc["id"],
        resource_label=inc.get("title"),
        request=request,
        success=True,
        metadata={
            "linked_event_ids": inc.get("linked_event_ids"),
            "priority": inc.get("priority"),
            "source": "promoted" if body.get("event_id") or body.get("from_event_id") else "manual",
        },
    )
    return web.json_response({"ok": True, "incident": inc}, status=201)


async def ccc_incident_update_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        inc = await update_incident(request.match_info.get("id") or "", user, patch=body)
    except IncidentValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except IncidentPermissionError as exc:
        return web.json_response({"error": str(exc)}, status=403)

    await write_audit(
        action=ACTION_CCC_INCIDENT_UPDATED,
        actor=user,
        resource_type="ccc_incident",
        resource_id=inc["id"],
        resource_label=inc.get("title"),
        request=request,
        success=True,
        metadata={"keys": sorted(body.keys())},
    )
    return web.json_response({"ok": True, "incident": inc})


async def ccc_incident_assign_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    inc_id = request.match_info.get("id") or ""
    reason = str(body.get("reason") or "manual_redirect")
    result = await assign_incident_responder(
        inc_id,
        actor=user,
        assignee_group=body.get("assignee_group"),
        assignee_user_id=body.get("assignee_user_id"),
        assignee_user_name=body.get("assignee_user_name"),
        reason=reason,
    )
    if not result:
        return web.json_response({"error": "Incident not found"}, status=404)
    action = (
        ACTION_CCC_INCIDENT_ESCALATED
        if "escalat" in reason
        else ACTION_CCC_INCIDENT_ASSIGNED
    )
    await write_audit(
        action=action,
        actor=user,
        resource_type="ccc_incident",
        resource_id=result["id"],
        resource_label=result.get("title"),
        request=request,
        success=True,
        metadata={
            "assignee_group": result.get("assignee_group"),
            "assignee_user_id": result.get("assignee_user_id"),
            "reason": reason,
            "dmr_tetra": False,
        },
    )
    return web.json_response({"ok": True, "incident": result})


async def ccc_process_escalations_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    result = await process_due_escalations()
    return web.json_response({"ok": True, **result})


async def ccc_sop_list_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    enabled_only = _bool_q(request.query.get("enabled_only"))
    items = await list_sop_workflows(enabled_only=enabled_only)
    return web.json_response({"items": items, "total": len(items), "hot_reload": True})


async def ccc_sop_get_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    wf = await get_sop_workflow(request.match_info.get("id") or "")
    if not wf:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(wf)


async def ccc_sop_create_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        wf = await create_sop_workflow(
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            steps=body.get("steps"),
            escalation_rules=body.get("escalation_rules"),
            enabled=bool(body.get("enabled", True)),
        )
    except SopValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_SOP_CREATED,
        actor=user,
        resource_type="ccc_sop",
        resource_id=wf["id"],
        resource_label=wf.get("name"),
        request=request,
        success=True,
        metadata={"steps": len(wf.get("steps") or [])},
    )
    return web.json_response({"ok": True, "workflow": wf}, status=201)


async def ccc_sop_update_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        wf = await update_sop_workflow(request.match_info.get("id") or "", body)
    except SopValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_SOP_UPDATED,
        actor=user,
        resource_type="ccc_sop",
        resource_id=wf["id"],
        resource_label=wf.get("name"),
        request=request,
        success=True,
        metadata={"keys": sorted(body.keys()), "hot_reload": True},
    )
    return web.json_response({"ok": True, "workflow": wf})


async def ccc_sop_delete_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    wid = request.match_info.get("id") or ""
    ok = await delete_sop_workflow(wid)
    if not ok:
        return web.json_response({"error": "Not found"}, status=404)
    await write_audit(
        action=ACTION_CCC_SOP_DELETED,
        actor=user,
        resource_type="ccc_sop",
        resource_id=wid,
        request=request,
        success=True,
        metadata={},
    )
    return web.json_response({"ok": True, "deleted": True})


def setup_ccc_incident_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/event-log", ccc_event_log_endpoint)
    app.router.add_get("/api/ccc/incidents", ccc_incidents_list_endpoint)
    app.router.add_post("/api/ccc/incidents", ccc_incident_create_endpoint)
    # Static path before {id} so "process-escalations" is not captured as an id.
    app.router.add_post("/api/ccc/incidents/process-escalations", ccc_process_escalations_endpoint)
    app.router.add_get("/api/ccc/incidents/{id}", ccc_incident_get_endpoint)
    app.router.add_patch("/api/ccc/incidents/{id}", ccc_incident_update_endpoint)
    app.router.add_post("/api/ccc/incidents/{id}/assign", ccc_incident_assign_endpoint)
    app.router.add_get("/api/ccc/sop-workflows", ccc_sop_list_endpoint)
    app.router.add_post("/api/ccc/sop-workflows", ccc_sop_create_endpoint)
    app.router.add_get("/api/ccc/sop-workflows/{id}", ccc_sop_get_endpoint)
    app.router.add_patch("/api/ccc/sop-workflows/{id}", ccc_sop_update_endpoint)
    app.router.add_delete("/api/ccc/sop-workflows/{id}", ccc_sop_delete_endpoint)
