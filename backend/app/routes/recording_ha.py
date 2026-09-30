"""API routes for RDSO 18.3.3 recording-server redundancy."""

from __future__ import annotations

import logging

from aiohttp import web

from app.core.access_control import deny_unless_super_admin
from app.services import recording_ha_coordinator as ha
from app.services.recording_ha_store import get_ha_store
from app.services.recording_ha_types import public_server
from app.services.recording_server_config import local_recording_server_id
from app.services.client_media_routing import list_recording_servers_public

logger = logging.getLogger(__name__)


async def ha_status_endpoint(request: web.Request):
    """GET /api/recordings/ha/status"""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    return web.json_response(await ha.ha_status_summary())


async def ha_heartbeat_endpoint(request: web.Request):
    """POST /api/recordings/ha/heartbeat — manual heartbeat (also used by tests/ops)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    doc = await ha.heartbeat_local_server()
    return web.json_response({"ok": True, "server": doc})


async def ha_assign_camera_endpoint(request: web.Request):
    """POST /api/recordings/ha/assign
    Body: {camera_id, recording_server_id}
    """
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    camera_id = str((body or {}).get("camera_id") or "").strip()
    server_id = str((body or {}).get("recording_server_id") or "").strip()
    if not camera_id or not server_id:
        return web.json_response(
            {"error": "camera_id and recording_server_id required"}, status=400
        )
    result = await ha.assign_camera_home(camera_id, server_id)
    return web.json_response(result)


async def ha_failover_endpoint(request: web.Request):
    """POST /api/recordings/ha/failover — run standby takeover pass (or for one primary)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    primary_id = str((body or {}).get("primary_id") or "").strip()
    if primary_id:
        result = await ha.standby_takeover_for_primary(primary_id)
    else:
        result = await ha.run_failover_pass()
    status = 200 if result.get("ok") else 409
    return web.json_response(result, status=status)


async def ha_failback_endpoint(request: web.Request):
    """POST /api/recordings/ha/failback
    Body: {primary_id, camera_ids?, force?: false, release?: true}
    Controlled failback — does not blindly move active recordings.
    """
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    primary_id = str((body or {}).get("primary_id") or local_recording_server_id()).strip()
    camera_ids = (body or {}).get("camera_ids")
    if camera_ids is not None and not isinstance(camera_ids, list):
        return web.json_response({"error": "camera_ids must be a list"}, status=400)
    force = (body or {}).get("force") is True
    do_release = (body or {}).get("release", True) is True

    marked = await ha.request_failback(primary_id, camera_ids=camera_ids)
    released = {"released": [], "held": []}
    if do_release:
        # Release on whatever currently owns (often standby)
        store = get_ha_store()
        owners = set()
        for own in await store.list_ownership():
            if own.get("failback_requested") and own.get("owner_server_id"):
                owners.add(own["owner_server_id"])
        all_released = []
        all_held = []
        for owner in owners:
            part = await ha.process_failback_releases(owner_server_id=owner, force=force)
            all_released.extend(part.get("released") or [])
            all_held.extend(part.get("held") or [])
        released = {"released": all_released, "held": all_held, "force": force}

    reclaimed = await ha.reclaim_after_failback(primary_id)
    return web.json_response(
        {
            "ok": bool(marked.get("ok")),
            "marked": marked,
            "release": released,
            "reclaimed": reclaimed,
        }
    )


async def ha_register_server_endpoint(request: web.Request):
    """POST /api/recordings/ha/servers — upsert a logical recording server (ops/test)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    server_id = str((body or {}).get("server_id") or "").strip()
    if not server_id:
        return web.json_response({"error": "server_id required"}, status=400)
    role = str((body or {}).get("role") or "primary").strip().lower()
    if role not in ("primary", "standby"):
        return web.json_response({"error": "role must be primary|standby"}, status=400)
    store = get_ha_store()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    healthy = (body or {}).get("healthy")
    if healthy is None:
        healthy = True
    doc = await store.upsert_server(
        server_id,
        {
            "role": role,
            "enabled": (body or {}).get("enabled", True) is not False,
            "healthy": bool(healthy),
            "last_seen": (body or {}).get("last_seen") or now,
            "hostname": (body or {}).get("hostname") or server_id,
            "protects_primary_ids": list((body or {}).get("protects_primary_ids") or []),
        },
    )
    return web.json_response({"ok": True, "server": public_server(doc)})


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


async def ha_list_servers_endpoint(request: web.Request):
    """GET /api/recordings/ha/servers — registered recording servers (unbounded, 18.1.17).

    Query: healthy=true|false, enabled=true|false, limit= (optional page size only).
    Does not fabricate physical hosts — returns registry entries only.
    """
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    q = request.rel_url.query
    healthy = _bool_query(q.get("healthy"))
    enabled = _bool_query(q.get("enabled"))
    limit_raw = q.get("limit")
    limit = None
    if limit_raw not in (None, ""):
        try:
            limit = int(limit_raw)
        except ValueError:
            return web.json_response({"error": "Invalid limit"}, status=400)
    data = await list_recording_servers_public(
        healthy_only=healthy is True,
        enabled_only=enabled is True,
        limit=limit,
    )
    return web.json_response(data)


def setup_recording_ha_routes(app: web.Application) -> None:
    app.router.add_get("/api/recordings/ha/status", ha_status_endpoint)
    app.router.add_get("/api/recordings/ha/servers", ha_list_servers_endpoint)
    app.router.add_post("/api/recordings/ha/heartbeat", ha_heartbeat_endpoint)
    app.router.add_post("/api/recordings/ha/assign", ha_assign_camera_endpoint)
    app.router.add_post("/api/recordings/ha/failover", ha_failover_endpoint)
    app.router.add_post("/api/recordings/ha/failback", ha_failback_endpoint)
    app.router.add_post("/api/recordings/ha/servers", ha_register_server_endpoint)
