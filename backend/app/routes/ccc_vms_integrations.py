"""RDSO 18.6.17.3 — Admin external VMS integration + alert ingest."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_admin_or_live_view
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_vms_adapter_errors import VmsAdapterError
from app.services.ccc_vms_alert_ingest import VmsAlertIngestError, ingest_vms_alert
from app.services.ccc_vms_integration_store import (
    VmsIntegrationError,
    create_integration,
    delete_integration,
    get_integration,
    get_integration_public,
    list_integrations,
    rotate_ingest_secret,
    update_integration,
    verify_ingest_secret,
)
from app.services.ccc_vms_source import (
    adapter_error_response,
    get_ccc_source_async,
    reload_external_vms_sources,
)

logger = logging.getLogger(__name__)

ACTION_VMS_INT_CREATED = "CCC_VMS_INTEGRATION_CREATED"
ACTION_VMS_INT_UPDATED = "CCC_VMS_INTEGRATION_UPDATED"
ACTION_VMS_INT_DELETED = "CCC_VMS_INTEGRATION_DELETED"
ACTION_VMS_INT_TEST = "CCC_VMS_INTEGRATION_TESTED"
ACTION_VMS_INT_ROTATE = "CCC_VMS_INTEGRATION_SECRET_ROTATED"
ACTION_VMS_ALERT_INGEST = "CCC_VMS_ALERT_INGESTED"


def _ingest_secret_from_request(request: web.Request) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("X-VMS-Integration-Secret")
        or request.headers.get("X-CCC-Device-Secret")
        or ""
    ).strip()


async def vms_integrations_list(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    items = await list_integrations()
    return web.json_response({"items": items, "total": len(items), "fake_vendors": False})


async def vms_integrations_create(request: web.Request) -> web.Response:
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
        pub, _ = await create_integration(body)
    except VmsIntegrationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await reload_external_vms_sources()
    await write_audit(
        action=ACTION_VMS_INT_CREATED,
        actor=user,
        resource_type="ccc_vms_integration",
        resource_id=pub.get("source_id"),
        resource_label=pub.get("label"),
        request=request,
        success=True,
        metadata={"vendor_type": pub.get("vendor_type"), "enabled": pub.get("enabled")},
    )
    return web.json_response({"ok": True, "integration": pub}, status=201)


async def vms_integrations_get(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    sid = request.match_info.get("sourceId") or ""
    pub = await get_integration_public(sid)
    if not pub:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(pub)


async def vms_integrations_update(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    sid = request.match_info.get("sourceId") or ""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        pub = await update_integration(sid, body)
    except VmsIntegrationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await reload_external_vms_sources()
    await write_audit(
        action=ACTION_VMS_INT_UPDATED,
        actor=user,
        resource_type="ccc_vms_integration",
        resource_id=pub.get("source_id"),
        request=request,
        success=True,
        metadata={"enabled": pub.get("enabled")},
    )
    return web.json_response({"ok": True, "integration": pub})


async def vms_integrations_delete(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    sid = request.match_info.get("sourceId") or ""
    ok = await delete_integration(sid)
    if not ok:
        return web.json_response({"error": "Not found"}, status=404)
    await reload_external_vms_sources()
    await write_audit(
        action=ACTION_VMS_INT_DELETED,
        actor=user,
        resource_type="ccc_vms_integration",
        resource_id=sid,
        request=request,
        success=True,
    )
    return web.json_response({"ok": True})


async def vms_integrations_test(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    sid = request.match_info.get("sourceId") or ""
    try:
        source = await get_ccc_source_async(sid)
        result = await source.health_status()
    except KeyError:
        return web.json_response({"error": "Source not found or disabled"}, status=404)
    except VmsAdapterError as exc:
        status, body = adapter_error_response(exc)
        result = body
        await write_audit(
            action=ACTION_VMS_INT_TEST,
            actor=user,
            resource_type="ccc_vms_integration",
            resource_id=sid,
            request=request,
            success=False,
            metadata={"code": exc.code},
        )
        return web.json_response({"ok": False, **result}, status=status)
    ok = result.get("status") == "healthy"
    await write_audit(
        action=ACTION_VMS_INT_TEST,
        actor=user,
        resource_type="ccc_vms_integration",
        resource_id=sid,
        request=request,
        success=ok,
        metadata={"status": result.get("status")},
    )
    return web.json_response({"ok": ok, **result})


async def vms_integrations_rotate_secret(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    sid = request.match_info.get("sourceId") or ""
    try:
        pub, _ = await rotate_ingest_secret(sid)
    except VmsIntegrationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_VMS_INT_ROTATE,
        actor=user,
        resource_type="ccc_vms_integration",
        resource_id=sid,
        request=request,
        success=True,
    )
    return web.json_response({"ok": True, "integration": pub})


async def vms_integrations_alerts_ingest(request: web.Request) -> web.Response:
    """Webhook: external VMS → normalized create_event (existing pipeline)."""
    sid = request.match_info.get("sourceId") or ""
    secret = _ingest_secret_from_request(request)
    if not await verify_ingest_secret(sid, secret):
        return web.json_response({"error": "Unauthorized"}, status=401)
    doc = await get_integration(sid)
    if not doc or not doc.get("enabled"):
        return web.json_response({"error": "Integration disabled"}, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        result = await ingest_vms_alert(
            source_id=sid,
            body=body,
            idempotency_header=(request.headers.get("Idempotency-Key") or "").strip(),
        )
    except VmsAlertIngestError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await write_audit(
        action=ACTION_VMS_ALERT_INGEST,
        actor={"id": f"vms:{sid}", "name": sid, "role": "integration"},
        resource_type="event",
        resource_id=result.get("event_id"),
        request=request,
        success=True,
        metadata={"source_id": sid, "via_existing_pipeline": True},
    )
    return web.json_response(result, status=201)


async def ccc_playback_search_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin_or_live_view(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    source_id = (request.query.get("source") or "local").strip() or "local"
    camera_id = (request.query.get("camera_id") or request.query.get("camera") or "").strip()
    if not camera_id:
        return web.json_response({"error": "camera_id required"}, status=400)
    try:
        source = await get_ccc_source_async(source_id)
        data = await source.search_playback_public(
            user,
            camera_id,
            from_ts=request.query.get("from") or "",
            to_ts=request.query.get("to") or "",
        )
    except KeyError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except VmsAdapterError as exc:
        status, body = adapter_error_response(exc)
        return web.json_response(body, status=status)
    return web.json_response(data)


def setup_ccc_vms_integration_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/admin/vms-integrations", vms_integrations_list)
    app.router.add_post("/api/ccc/admin/vms-integrations", vms_integrations_create)
    app.router.add_get("/api/ccc/admin/vms-integrations/{sourceId}", vms_integrations_get)
    app.router.add_patch("/api/ccc/admin/vms-integrations/{sourceId}", vms_integrations_update)
    app.router.add_delete("/api/ccc/admin/vms-integrations/{sourceId}", vms_integrations_delete)
    app.router.add_post(
        "/api/ccc/admin/vms-integrations/{sourceId}/test", vms_integrations_test
    )
    app.router.add_post(
        "/api/ccc/admin/vms-integrations/{sourceId}/rotate-secret",
        vms_integrations_rotate_secret,
    )
    app.router.add_post(
        "/api/ccc/vms-integrations/{sourceId}/alerts", vms_integrations_alerts_ingest
    )
    app.router.add_get("/api/ccc/playback/search", ccc_playback_search_endpoint)
