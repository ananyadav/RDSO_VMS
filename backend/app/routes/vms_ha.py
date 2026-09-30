"""API routes for RDSO 18.1.29 VMS management-server N:1 redundancy.

Separate from /api/recordings/ha/* (NVR / recording-server HA).
"""

from __future__ import annotations

from aiohttp import web

from app.core.access_control import deny_unless_super_admin
from app.services import vms_ha_coordinator as vms_ha
from app.services.vms_ha_store import get_vms_ha_store
from app.services.vms_ha_types import public_vms_node
from app.services.vms_server_config import local_vms_server_id


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


async def vms_ha_status_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    return web.json_response(await vms_ha.vms_ha_status_summary())


async def vms_ha_list_nodes_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    healthy = _bool_query(q.get("healthy"))
    limit_raw = q.get("limit")
    limit = None
    if limit_raw not in (None, ""):
        try:
            limit = int(limit_raw)
        except ValueError:
            return web.json_response({"error": "Invalid limit"}, status=400)
    data = await vms_ha.list_vms_nodes_public(
        healthy_only=healthy is True,
        limit=limit,
    )
    return web.json_response(data)


async def vms_ha_heartbeat_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    doc = await vms_ha.heartbeat_local_node()
    lease = await vms_ha.claim_or_renew_coordinator_lease()
    return web.json_response({"ok": True, "node": public_vms_node(doc), "lease": lease})


async def vms_ha_register_node_endpoint(request: web.Request) -> web.Response:
    """POST /api/vms/ha/nodes — upsert a logical VMS node (ops / acceptance)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    server_id = str((body or {}).get("vms_server_id") or (body or {}).get("server_id") or "").strip()
    if not server_id:
        return web.json_response({"error": "vms_server_id required"}, status=400)
    role = str((body or {}).get("role") or "primary").strip().lower()
    if role not in ("primary", "standby"):
        return web.json_response({"error": "role must be primary|standby"}, status=400)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    healthy = (body or {}).get("healthy")
    if healthy is None:
        healthy = True
    store = get_vms_ha_store()
    doc = await store.upsert_node(
        server_id,
        {
            "role": role,
            "enabled": (body or {}).get("enabled", True) is not False,
            "healthy": bool(healthy),
            "is_leader": bool((body or {}).get("is_leader")),
            "state": (body or {}).get("state") or "follower",
            "last_seen": (body or {}).get("last_seen") or now,
            "hostname": (body or {}).get("hostname") or server_id,
        },
    )
    return web.json_response({"ok": True, "node": public_vms_node(doc)})


async def vms_readiness_endpoint(request: web.Request) -> web.Response:
    """GET /api/vms/ha/ready — public readiness for LB / ops (no secrets)."""
    from app.core.startup_state import get_startup
    from app.services.vms_server_config import vms_ha_enabled

    state = get_startup(request.app)
    return web.json_response(
        {
            "ready": bool(state.get("ready")),
            "mongodb": bool(state.get("mongodb")),
            "vms_server_id": local_vms_server_id(),
            "ha_enabled": vms_ha_enabled(),
            "local_is_leader": await vms_ha.local_is_coordinator(),
            "rdso_18_1_29": True,
        }
    )


def setup_vms_ha_routes(app: web.Application) -> None:
    app.router.add_get("/api/vms/ha/status", vms_ha_status_endpoint)
    app.router.add_get("/api/vms/ha/nodes", vms_ha_list_nodes_endpoint)
    app.router.add_get("/api/vms/ha/ready", vms_readiness_endpoint)
    app.router.add_post("/api/vms/ha/heartbeat", vms_ha_heartbeat_endpoint)
    app.router.add_post("/api/vms/ha/nodes", vms_ha_register_node_endpoint)
