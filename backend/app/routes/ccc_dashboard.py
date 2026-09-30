"""RDSO 18.6.5 / 18.6.6 / 18.6.15.4 / 18.6.16.1 — CCC dashboard, prefs, hot screen, groups, comms."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import (
    deny_unless_admin,
    deny_unless_events_permission,
    require_user,
)
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_comms_service import (
    CccCommsError,
    acknowledge_communication,
    create_message_template,
    list_incident_communications,
    list_message_templates,
    send_incident_communication,
    update_message_template,
)
from app.services.ccc_compliance_service import get_incident_compliance
from app.services.ccc_dashboard_prefs import get_dashboard_prefs, save_dashboard_prefs
from app.services.ccc_dashboard_service import get_ccc_dashboard
from app.services.ccc_groups_service import (
    CccGroupError,
    create_ccc_group,
    list_ccc_groups,
    update_ccc_group,
)
from app.services.ccc_hot_screen_service import list_hot_screen_items

logger = logging.getLogger(__name__)

ACTION_CCC_DASH_PREFS = "CCC_DASHBOARD_PREFS_UPDATED"
ACTION_CCC_GROUP_CREATED = "CCC_GROUP_CREATED"
ACTION_CCC_GROUP_UPDATED = "CCC_GROUP_UPDATED"
ACTION_CCC_MSG_TEMPLATE = "CCC_MESSAGE_TEMPLATE_CHANGED"
ACTION_CCC_COMM_SENT = "CCC_COMMUNICATION_SENT"
ACTION_CCC_COMM_ACK = "CCC_COMMUNICATION_ACKNOWLEDGED"


async def ccc_dashboard_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    data = await get_ccc_dashboard(user=user)
    prefs = await get_dashboard_prefs(user)
    data["prefs"] = prefs
    return web.json_response(data)


async def ccc_dashboard_prefs_get(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(await get_dashboard_prefs(user))


async def ccc_dashboard_prefs_put(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        prefs = await save_dashboard_prefs(user, widgets=body.get("widgets"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_DASH_PREFS,
        actor=user,
        resource_type="ccc_dashboard_prefs",
        resource_id=prefs.get("user_id"),
        request=request,
        success=True,
        metadata={"widget_count": len(prefs.get("widgets") or [])},
    )
    return web.json_response({"ok": True, "prefs": prefs})


async def ccc_hot_screen_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        limit = int(request.query.get("limit") or 20)
    except ValueError:
        limit = 20
    data = await list_hot_screen_items(user=user, limit=limit)
    return web.json_response(data)


async def ccc_groups_list(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    kind = request.query.get("kind")
    items = await list_ccc_groups(kind=kind)
    return web.json_response({"items": items, "total": len(items), "not_rbac_roles": True})


async def ccc_groups_create(request: web.Request) -> web.Response:
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
        group = await create_ccc_group(
            name=str(body.get("name") or ""),
            kind=str(body.get("kind") or "custom"),
            member_user_ids=body.get("member_user_ids"),
            description=str(body.get("description") or ""),
        )
    except CccGroupError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_GROUP_CREATED,
        actor=user,
        resource_type="ccc_group",
        resource_id=group["id"],
        resource_label=group.get("name"),
        request=request,
        success=True,
        metadata={"kind": group.get("kind")},
    )
    return web.json_response({"ok": True, "group": group}, status=201)


async def ccc_groups_update(request: web.Request) -> web.Response:
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
        group = await update_ccc_group(request.match_info.get("id") or "", body)
    except CccGroupError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_GROUP_UPDATED,
        actor=user,
        resource_type="ccc_group",
        resource_id=group["id"],
        resource_label=group.get("name"),
        request=request,
        success=True,
        metadata={"keys": sorted(body.keys())},
    )
    return web.json_response({"ok": True, "group": group})


async def ccc_templates_list(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    items = await list_message_templates()
    return web.json_response(
        {"items": items, "total": len(items), "external_delivery": False, "dmr_tetra": False}
    )


async def ccc_templates_create(request: web.Request) -> web.Response:
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
        tpl = await create_message_template(
            name=str(body.get("name") or ""), body=str(body.get("body") or "")
        )
    except CccCommsError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_MSG_TEMPLATE,
        actor=user,
        resource_type="ccc_message_template",
        resource_id=tpl["id"],
        resource_label=tpl.get("name"),
        request=request,
        success=True,
        metadata={"op": "create"},
    )
    return web.json_response({"ok": True, "template": tpl}, status=201)


async def ccc_templates_update(request: web.Request) -> web.Response:
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
        tpl = await update_message_template(request.match_info.get("id") or "", body)
    except CccCommsError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_MSG_TEMPLATE,
        actor=user,
        resource_type="ccc_message_template",
        resource_id=tpl["id"],
        resource_label=tpl.get("name"),
        request=request,
        success=True,
        metadata={"op": "update"},
    )
    return web.json_response({"ok": True, "template": tpl})


async def ccc_comm_send(request: web.Request) -> web.Response:
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
        comm = await send_incident_communication(
            user,
            incident_id=str(body.get("incident_id") or ""),
            body=str(body.get("body") or ""),
            subject=str(body.get("subject") or ""),
            template_id=body.get("template_id"),
            recipient_user_id=body.get("recipient_user_id"),
            recipient_group=body.get("recipient_group"),
        )
    except CccCommsError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_COMM_SENT,
        actor=user,
        resource_type="ccc_communication",
        resource_id=comm["id"],
        request=request,
        success=True,
        metadata={
            "incident_id": comm.get("incident_id"),
            "channel": "ccc_internal",
            "external_delivery": False,
        },
    )
    return web.json_response({"ok": True, "communication": comm}, status=201)


async def ccc_comm_list(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    incident_id = request.match_info.get("incidentId") or ""
    items = await list_incident_communications(incident_id)
    return web.json_response({"items": items, "total": len(items)})


async def ccc_comm_ack(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    try:
        comm = await acknowledge_communication(request.match_info.get("id") or "", user)
    except CccCommsError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_COMM_ACK,
        actor=user,
        resource_type="ccc_communication",
        resource_id=comm["id"],
        request=request,
        success=True,
        metadata={"status": "acknowledged"},
    )
    return web.json_response({"ok": True, "communication": comm})


async def ccc_compliance_get(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    data = await get_incident_compliance(request.match_info.get("incidentId") or "")
    if not data:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(data)


def setup_ccc_dashboard_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/dashboard", ccc_dashboard_endpoint)
    app.router.add_get("/api/ccc/dashboard/prefs", ccc_dashboard_prefs_get)
    app.router.add_put("/api/ccc/dashboard/prefs", ccc_dashboard_prefs_put)
    app.router.add_get("/api/ccc/hot-screen", ccc_hot_screen_endpoint)
    app.router.add_get("/api/ccc/groups", ccc_groups_list)
    app.router.add_post("/api/ccc/groups", ccc_groups_create)
    app.router.add_patch("/api/ccc/groups/{id}", ccc_groups_update)
    app.router.add_get("/api/ccc/message-templates", ccc_templates_list)
    app.router.add_post("/api/ccc/message-templates", ccc_templates_create)
    app.router.add_patch("/api/ccc/message-templates/{id}", ccc_templates_update)
    app.router.add_post("/api/ccc/communications", ccc_comm_send)
    app.router.add_get(
        "/api/ccc/incidents/{incidentId}/communications", ccc_comm_list
    )
    app.router.add_post("/api/ccc/communications/{id}/acknowledge", ccc_comm_ack)
    app.router.add_get(
        "/api/ccc/incidents/{incidentId}/compliance", ccc_compliance_get
    )
