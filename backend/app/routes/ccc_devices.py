"""RDSO 18.6.22.3 / .12 / .15 — CCC devices, ingest, admin, pre-emption."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_device_ingest import CccIngestError, authenticate_device_ingest, ingest_device_alert
from app.services.ccc_device_service import (
    CccDeviceError,
    create_device,
    delete_device,
    get_device,
    get_device_doc,
    list_devices,
    touch_device_heartbeat,
    update_device,
)
from app.services.ccc_preemption import (
    admin_gui_options_public,
    evaluate_preemption,
    get_preemption_policy,
    save_preemption_policy,
)
from app.services.priority_levels import normalize_priority

logger = logging.getLogger(__name__)

ACTION_CCC_DEVICE_CREATED = "CCC_DEVICE_CREATED"
ACTION_CCC_DEVICE_UPDATED = "CCC_DEVICE_UPDATED"
ACTION_CCC_DEVICE_DELETED = "CCC_DEVICE_DELETED"
ACTION_CCC_DEVICE_INGEST = "CCC_DEVICE_INGEST"
ACTION_CCC_PREEMPTION_POLICY_UPDATED = "CCC_PREEMPTION_POLICY_UPDATED"


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    v = raw.strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return None


def _extract_ingest_secret(request: web.Request, body: dict) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("X-CCC-Device-Secret") or request.headers.get("X-Api-Key") or ""
    if header.strip():
        return header.strip()
    return str(body.get("integration_secret") or body.get("api_key") or "").strip()


async def ccc_devices_list(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    data = await list_devices(
        type=q.get("type"),
        enabled=_bool_query(q.get("enabled")),
        status=q.get("status"),
        location=q.get("location"),
        q=q.get("q") or "",
        limit=limit,
        offset=offset,
    )
    return web.json_response(data)


async def ccc_devices_get(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    item = await get_device(request.match_info.get("id") or "")
    if not item:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(item)


async def ccc_devices_create(request: web.Request) -> web.Response:
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
        device = await create_device(
            name=str(body.get("name") or ""),
            type=str(body.get("type") or "external_sensor"),
            location=str(body.get("location") or ""),
            device_uid=body.get("device_uid"),
            linked_camera_ids=body.get("linked_camera_ids"),
            linked_vms_source=body.get("linked_vms_source"),
            capabilities=body.get("capabilities"),
            metadata=body.get("metadata"),
            default_priority=body.get("default_priority"),
            enabled=bool(body.get("enabled", True)),
            generate_secret=bool(body.get("generate_secret", True)),
        )
    except CccDeviceError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_DEVICE_CREATED,
        actor=user,
        resource_type="ccc_device",
        resource_id=device["id"],
        resource_label=device.get("name"),
        request=request,
        success=True,
        metadata={"type": device.get("type"), "enabled": device.get("enabled")},
    )
    return web.json_response({"ok": True, "device": device}, status=201)


async def ccc_devices_update(request: web.Request) -> web.Response:
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
        device = await update_device(request.match_info.get("id") or "", body)
    except CccDeviceError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_DEVICE_UPDATED,
        actor=user,
        resource_type="ccc_device",
        resource_id=device["id"],
        resource_label=device.get("name"),
        request=request,
        success=True,
        metadata={"keys": sorted(body.keys()), "enabled": device.get("enabled")},
    )
    return web.json_response({"ok": True, "device": device})


async def ccc_devices_delete(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    device_id = request.match_info.get("id") or ""
    try:
        ok = await delete_device(device_id)
    except CccDeviceError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not ok:
        return web.json_response({"error": "Not found"}, status=404)
    await write_audit(
        action=ACTION_CCC_DEVICE_DELETED,
        actor=user,
        resource_type="ccc_device",
        resource_id=device_id,
        request=request,
        success=True,
    )
    return web.json_response({"ok": True})


async def ccc_device_ingest(request: web.Request) -> web.Response:
    """Authenticated integration interface — Bearer / X-CCC-Device-Secret / body secret."""
    device_ref = request.match_info.get("id") or ""
    doc = await get_device_doc(device_ref)
    if not doc:
        return web.json_response({"error": "Unknown device"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    secret = _extract_ingest_secret(request, body)
    try:
        await authenticate_device_ingest(doc, secret)
        result = await ingest_device_alert(
            doc,
            body=body,
            idempotency_header=(request.headers.get("Idempotency-Key") or "").strip(),
        )
    except CccIngestError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    # Best-effort audit without storing secret
    try:
        await write_audit(
            action=ACTION_CCC_DEVICE_INGEST,
            actor={"id": str(doc["_id"]), "name": doc.get("device_uid") or "device", "role": "ccc_device"},
            resource_type="ccc_device",
            resource_id=str(doc["_id"]),
            request=request,
            success=True,
            metadata={
                "event_created": result.get("event_created"),
                "pipeline": result.get("pipeline"),
            },
        )
    except Exception:
        pass
    return web.json_response(result, status=201 if result.get("event_created") else 200)


async def ccc_device_heartbeat(request: web.Request) -> web.Response:
    device_ref = request.match_info.get("id") or ""
    doc = await get_device_doc(device_ref)
    if not doc:
        return web.json_response({"error": "Unknown device"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    secret = _extract_ingest_secret(request, body)
    try:
        await authenticate_device_ingest(doc, secret)
        device = await touch_device_heartbeat(
            doc,
            status=str(body.get("status") or "online"),
            health=str(body.get("health") or "ok"),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
        )
    except CccIngestError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "device": device})


async def ccc_preemption_get(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    return web.json_response(await get_preemption_policy())


async def ccc_preemption_put(request: web.Request) -> web.Response:
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
        policy = await save_preemption_policy(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_CCC_PREEMPTION_POLICY_UPDATED,
        actor=user,
        resource_type="ccc_preemption_policy",
        resource_id="ccc_preemption",
        request=request,
        success=True,
        metadata={"equal_priority": policy.get("equal_priority"), "enabled": policy.get("enabled")},
    )
    return web.json_response({"ok": True, "policy": policy})


async def ccc_preemption_evaluate(request: web.Request) -> web.Response:
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    try:
        actor = normalize_priority(q.get("actor_priority") or 1)
        holder = normalize_priority(q.get("holder_priority") or 1)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)
    policy = await get_preemption_policy()
    decision = evaluate_preemption(
        actor_priority=actor,
        holder_priority=holder,
        scope=str(q.get("scope") or "ccc_control"),
        policy=policy,
    )
    return web.json_response({"decision": decision, "policy": policy})


async def ccc_admin_gui_options(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    return web.json_response(admin_gui_options_public())


def setup_ccc_device_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/devices", ccc_devices_list)
    app.router.add_post("/api/ccc/devices", ccc_devices_create)
    app.router.add_get("/api/ccc/devices/{id}", ccc_devices_get)
    app.router.add_patch("/api/ccc/devices/{id}", ccc_devices_update)
    app.router.add_delete("/api/ccc/devices/{id}", ccc_devices_delete)
    app.router.add_post("/api/ccc/devices/{id}/ingest", ccc_device_ingest)
    app.router.add_post("/api/ccc/devices/{id}/heartbeat", ccc_device_heartbeat)
    app.router.add_get("/api/ccc/admin/preemption-policy", ccc_preemption_get)
    app.router.add_put("/api/ccc/admin/preemption-policy", ccc_preemption_put)
    app.router.add_get("/api/ccc/admin/preemption-evaluate", ccc_preemption_evaluate)
    app.router.add_get("/api/ccc/admin/gui-options", ccc_admin_gui_options)
