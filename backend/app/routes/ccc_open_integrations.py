"""RDSO 18.6.22.8 — Open CCC third-party integration facade.

Delegates to existing device registry + VMS integration stacks.
No second event/alarm engine. GIS-format storage not claimed.
"""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_events_permission
from app.core.auth_context import get_effective_user
from app.services.audit_service import write_audit
from app.services.ccc_device_ingest import (
    CccIngestError,
    authenticate_device_ingest,
    ingest_device_alert,
)
from app.services.ccc_device_service import (
    CccDeviceError,
    create_device,
    get_device_doc,
    list_devices,
    touch_device_heartbeat,
)
from app.services.ccc_open_integration import (
    open_integration_contract,
    open_integration_identity,
)
from app.services.ccc_vms_alert_ingest import VmsAlertIngestError, ingest_vms_alert
from app.services.ccc_vms_integration_store import (
    VmsIntegrationError,
    create_integration,
    get_integration,
    list_integrations,
    verify_ingest_secret,
)
from app.services.ccc_vms_source import get_ccc_source_async, reload_external_vms_sources

logger = logging.getLogger(__name__)

ACTION_OPEN_REGISTER = "CCC_OPEN_INTEGRATION_REGISTERED"
ACTION_OPEN_EVENT = "CCC_OPEN_INTEGRATION_EVENT"


def _secret_from_request(request: web.Request, body: dict | None = None) -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    hdr = (
        request.headers.get("X-CCC-Device-Secret")
        or request.headers.get("X-VMS-Integration-Secret")
        or request.headers.get("X-Api-Key")
        or ""
    ).strip()
    if hdr:
        return hdr
    body = body or {}
    return str(body.get("integration_secret") or body.get("api_key") or "").strip()


async def open_capability(request: web.Request) -> web.Response:
    """Public capability discovery — no secrets in payload."""
    return web.json_response(open_integration_contract())


async def open_identity(request: web.Request) -> web.Response:
    return web.json_response(open_integration_identity())


async def open_register(request: web.Request) -> web.Response:
    """Admin: register/configure device or VMS integration via unified entry."""
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
    kind = str(body.get("kind") or body.get("target") or "device").strip().lower()
    try:
        if kind in ("device", "sensor", "external_sensor"):
            pub = await create_device(
                name=str(body.get("name") or body.get("label") or ""),
                type=str(body.get("type") or "external_sensor"),
                location=str(body.get("location") or ""),
                device_uid=body.get("device_uid") or body.get("source_id"),
                linked_camera_ids=body.get("linked_camera_ids"),
                linked_vms_source=body.get("linked_vms_source"),
                capabilities=body.get("capabilities")
                if isinstance(body.get("capabilities"), list)
                else None,
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
                default_priority=body.get("default_priority", 3),
                enabled=bool(body.get("enabled", True)),
                generate_secret=True,
            )
            await write_audit(
                action=ACTION_OPEN_REGISTER,
                actor=user,
                resource_type="ccc_device",
                resource_id=pub.get("id"),
                request=request,
                success=True,
                metadata={"kind": "device", "type": pub.get("type")},
            )
            return web.json_response(
                {
                    "ok": True,
                    "kind": "device",
                    "integration": pub,
                    "ingest_path": f"/api/ccc/devices/{pub.get('id')}/ingest",
                    "open_events_hint": {
                        "target": "device",
                        "device_id": pub.get("id"),
                    },
                    "second_alarm_engine": False,
                },
                status=201,
            )
        if kind in ("vms", "vms_integration", "external_vms"):
            pub, _ingest_secret = await create_integration(body)
            await reload_external_vms_sources()
            await write_audit(
                action=ACTION_OPEN_REGISTER,
                actor=user,
                resource_type="ccc_vms_integration",
                resource_id=pub.get("source_id"),
                request=request,
                success=True,
                metadata={"kind": "vms", "enabled": pub.get("enabled")},
            )
            # Secret shown once via public DTO when create_integration includes it.
            return web.json_response(
                {
                    "ok": True,
                    "kind": "vms",
                    "integration": pub,
                    "alert_path": f"/api/ccc/vms-integrations/{pub.get('source_id')}/alerts",
                    "open_events_hint": {
                        "target": "vms",
                        "source_id": pub.get("source_id"),
                    },
                    "second_alarm_engine": False,
                    "named_vendor_sdk": False,
                },
                status=201,
            )
        return web.json_response(
            {"error": "kind must be device|vms (no named vendor SDKs)"},
            status=400,
        )
    except (CccDeviceError, VmsIntegrationError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def open_health(request: web.Request) -> web.Response:
    """Heartbeat/status for a registered device or VMS source."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    target = str(body.get("target") or body.get("kind") or "device").strip().lower()
    secret = _secret_from_request(request, body)

    if target in ("device", "sensor"):
        device_id = str(body.get("device_id") or body.get("id") or "").strip()
        if not device_id:
            return web.json_response({"error": "device_id required"}, status=400)
        doc = await get_device_doc(device_id)
        if not doc:
            return web.json_response({"error": "Unknown device"}, status=404)
        try:
            await authenticate_device_ingest(doc, secret)
            device = await touch_device_heartbeat(
                doc,
                status=str(body.get("status") or "online"),
                health=str(body.get("health") or "ok"),
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
            )
        except CccIngestError as exc:
            status = 401 if "secret" in str(exc).lower() or "disabled" in str(exc).lower() else 400
            return web.json_response({"error": str(exc)}, status=status)
        return web.json_response({"ok": True, "target": "device", "device": device})

    if target in ("vms", "vms_integration"):
        sid = str(body.get("source_id") or body.get("id") or "").strip()
        if not sid:
            return web.json_response({"error": "source_id required"}, status=400)
        if not await verify_ingest_secret(sid, secret):
            return web.json_response({"error": "Unauthorized"}, status=401)
        integ = await get_integration(sid)
        if not integ or not integ.get("enabled"):
            return web.json_response({"error": "Integration disabled"}, status=403)
        try:
            source = await get_ccc_source_async(sid)
            health = await source.health_status()
        except KeyError:
            return web.json_response({"error": "Source not loaded (disabled or missing)"}, status=404)
        except Exception as exc:
            return web.json_response(
                {"ok": False, "target": "vms", "source_id": sid, "status": "offline", "error": str(exc)[:200]},
                status=503,
            )
        return web.json_response({"ok": True, "target": "vms", "source_id": sid, "health": health})

    return web.json_response({"error": "target must be device|vms"}, status=400)


async def open_devices(request: web.Request) -> web.Response:
    """List registered integration targets (devices + enabled VMS sources). Session ACL."""
    denied = await deny_unless_events_permission(request)
    if denied is not None:
        return denied
    try:
        limit = int(request.query.get("limit") or 100)
    except ValueError:
        limit = 100
    try:
        offset = int(request.query.get("offset") or 0)
    except ValueError:
        offset = 0
    devices = await list_devices(limit=limit, offset=offset)
    vms = await list_integrations(enabled_only=False)
    return web.json_response(
        {
            "ok": True,
            "devices": devices,
            "vms_integrations": vms,
            "gis": False,
            "gis_format_storage": False,
            "named_vendor_sdks_bundled": False,
            "second_alarm_engine": False,
        }
    )


async def open_events(request: web.Request) -> web.Response:
    """Unified event/alert ingest → existing create_event pipeline."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Malformed JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON object required"}, status=400)

    target = str(body.get("target") or body.get("kind") or "device").strip().lower()
    secret = _secret_from_request(request, body)
    idem = (request.headers.get("Idempotency-Key") or "").strip()

    try:
        if target in ("device", "sensor"):
            device_id = str(body.get("device_id") or body.get("id") or "").strip()
            if not device_id:
                return web.json_response({"error": "device_id required"}, status=400)
            doc = await get_device_doc(device_id)
            if not doc:
                return web.json_response({"error": "Unknown device"}, status=404)
            await authenticate_device_ingest(doc, secret)
            result = await ingest_device_alert(doc, body=body, idempotency_header=idem)
            try:
                await write_audit(
                    action=ACTION_OPEN_EVENT,
                    actor={
                        "id": str(doc["_id"]),
                        "name": doc.get("device_uid") or "device",
                        "role": "ccc_open_integration",
                    },
                    resource_type="event",
                    resource_id=result.get("event_id"),
                    request=request,
                    success=True,
                    metadata={
                        "target": "device",
                        "duplicate": result.get("duplicate"),
                        "pipeline": result.get("pipeline"),
                    },
                )
            except Exception:
                pass
            status = 200 if result.get("duplicate") else (201 if result.get("event_created") else 200)
            return web.json_response(result, status=status)

        if target in ("vms", "vms_integration"):
            sid = str(body.get("source_id") or body.get("id") or "").strip()
            if not sid:
                return web.json_response({"error": "source_id required"}, status=400)
            if not await verify_ingest_secret(sid, secret):
                return web.json_response({"error": "Unauthorized"}, status=401)
            integ = await get_integration(sid)
            if not integ or not integ.get("enabled"):
                return web.json_response({"error": "Integration disabled"}, status=403)
            result = await ingest_vms_alert(
                source_id=sid, body=body, idempotency_header=idem
            )
            try:
                await write_audit(
                    action=ACTION_OPEN_EVENT,
                    actor={"id": f"vms:{sid}", "name": sid, "role": "ccc_open_integration"},
                    resource_type="event",
                    resource_id=result.get("event_id"),
                    request=request,
                    success=True,
                    metadata={"target": "vms", "duplicate": result.get("duplicate")},
                )
            except Exception:
                pass
            status = 200 if result.get("duplicate") else 201
            return web.json_response(result, status=status)

        return web.json_response({"error": "target must be device|vms"}, status=400)
    except CccIngestError as exc:
        msg = str(exc)
        code = 401 if "secret" in msg.lower() else 400
        if "disabled" in msg.lower():
            code = 403
        if "rate limit" in msg.lower() or "exceeds" in msg.lower():
            code = 413 if "exceeds" in msg.lower() else 429
        return web.json_response({"error": msg}, status=code)
    except VmsAlertIngestError as exc:
        msg = str(exc)
        code = 413 if "exceeds" in msg.lower() else 400
        return web.json_response({"error": msg}, status=code)


def setup_ccc_open_integration_routes(app: web.Application) -> None:
    app.router.add_get("/api/ccc/integrations/open/capability", open_capability)
    app.router.add_get("/api/ccc/integrations/open/identity", open_identity)
    app.router.add_post("/api/ccc/integrations/open/register", open_register)
    app.router.add_post("/api/ccc/integrations/open/health", open_health)
    app.router.add_get("/api/ccc/integrations/open/devices", open_devices)
    app.router.add_post("/api/ccc/integrations/open/events", open_events)
