"""RDSO 18.1.29 — VMS management-server N:1 coordination (leader lease for singleton jobs).

Does NOT replace recording-server HA (18.3.3). Clients stay server-independent via
shared Mongo sessions/config and relative API paths.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from app.services.vms_ha_store import get_vms_ha_store
from app.services.vms_ha_types import (
    LEASE_VMS_COORDINATOR,
    public_vms_lease,
    public_vms_node,
)
from app.services.vms_server_config import (
    local_vms_role,
    local_vms_server_id,
    vms_ha_enabled,
    vms_heartbeat_interval_seconds,
    vms_heartbeat_timeout_seconds,
    vms_leader_ttl_seconds,
)

logger = logging.getLogger(__name__)

StartSingletons = Callable[[], Awaitable[None]]
StopSingletons = Callable[[], Awaitable[None]]

_heartbeat_task: asyncio.Task | None = None
_leader_task: asyncio.Task | None = None
_start_singletons: StartSingletons | None = None
_stop_singletons: StopSingletons | None = None
_local_is_leader = False
_local_lease_token: str | None = None
_singletons_running = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


def is_server_fresh(doc: dict[str, Any] | None) -> bool:
    if not doc or not doc.get("enabled", True):
        return False
    last = str(doc.get("last_seen") or "")
    if not last:
        return False
    try:
        seen = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (_utcnow() - seen.astimezone(timezone.utc)).total_seconds()
    return age <= vms_heartbeat_timeout_seconds()


async def refresh_node_health_flags() -> None:
    store = get_vms_ha_store()
    for doc in await store.list_nodes():
        sid = doc.get("vms_server_id")
        if not sid:
            continue
        healthy = is_server_fresh(doc)
        if bool(doc.get("healthy")) != healthy:
            await store.upsert_node(sid, {"healthy": healthy})


async def heartbeat_local_node(*, is_leader: bool | None = None) -> dict[str, Any]:
    store = get_vms_ha_store()
    sid = local_vms_server_id()
    leader = _local_is_leader if is_leader is None else bool(is_leader)
    return await store.upsert_node(
        sid,
        {
            "role": local_vms_role(),
            "enabled": True,
            "healthy": True,
            "is_leader": leader,
            "state": "leader" if leader else "follower",
            "last_seen": _iso(),
            "hostname": socket.gethostname() or sid,
        },
    )


async def claim_or_renew_coordinator_lease() -> dict[str, Any]:
    """Try to own the singleton coordinator lease. Prevents split-brain job execution."""
    global _local_is_leader, _local_lease_token
    store = get_vms_ha_store()
    sid = local_vms_server_id()
    now = _utcnow()
    expires = _iso(now + timedelta(seconds=vms_leader_ttl_seconds()))
    hb = _iso(now)
    token = _local_lease_token or secrets.token_hex(8)

    claimed = await store.try_claim_lease(
        lease_name=LEASE_VMS_COORDINATOR,
        owner_vms_server_id=sid,
        lease_token=token,
        expires_at=expires,
        heartbeat_at=hb,
    )
    if claimed:
        _local_is_leader = True
        _local_lease_token = claimed.get("lease_token") or token
        renewed = await store.renew_lease(
            lease_name=LEASE_VMS_COORDINATOR,
            owner_vms_server_id=sid,
            lease_token=_local_lease_token,
            expires_at=expires,
            heartbeat_at=hb,
        )
        return {"ok": True, "leader": True, "lease": public_vms_lease(renewed or claimed)}

    # Lost / never held
    cur = await store.get_lease(LEASE_VMS_COORDINATOR)
    if cur and cur.get("owner_vms_server_id") == sid:
        # Race: treat as not leader until reclaim
        pass
    _local_is_leader = False
    _local_lease_token = None
    return {
        "ok": True,
        "leader": False,
        "lease": public_vms_lease(cur),
        "owner": (cur or {}).get("owner_vms_server_id"),
    }


async def local_is_coordinator() -> bool:
    """True when this process should run singleton VMS jobs."""
    if not vms_ha_enabled():
        return True
    return bool(_local_is_leader)


def local_is_coordinator_sync() -> bool:
    """Non-async leadership peek for /api/health (HA off ⇒ always True)."""
    if not vms_ha_enabled():
        return True
    return bool(_local_is_leader)


async def _apply_leadership(want_leader: bool) -> None:
    global _singletons_running
    if want_leader and not _singletons_running:
        if _start_singletons:
            try:
                await _start_singletons()
                _singletons_running = True
                logger.info("[VMS-HA] Acquired coordinator lease — singleton jobs started")
            except Exception as exc:
                logger.error("[VMS-HA] Failed starting singleton jobs: %s", exc)
    elif not want_leader and _singletons_running:
        if _stop_singletons:
            try:
                await _stop_singletons()
            except Exception as exc:
                logger.warning("[VMS-HA] Error stopping singleton jobs: %s", exc)
        _singletons_running = False
        logger.info("[VMS-HA] Lost coordinator lease — singleton jobs stopped")


async def _heartbeat_loop() -> None:
    while True:
        try:
            await heartbeat_local_node()
            await refresh_node_health_flags()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[VMS-HA] Heartbeat error: %s", exc)
        await asyncio.sleep(vms_heartbeat_interval_seconds())


async def _leader_loop() -> None:
    while True:
        try:
            result = await claim_or_renew_coordinator_lease()
            await heartbeat_local_node(is_leader=bool(result.get("leader")))
            await _apply_leadership(bool(result.get("leader")))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[VMS-HA] Leader loop error: %s", exc)
        await asyncio.sleep(vms_heartbeat_interval_seconds())


async def vms_ha_status_summary() -> dict[str, Any]:
    await refresh_node_health_flags()
    store = get_vms_ha_store()
    nodes = [public_vms_node(n) for n in await store.list_nodes()]
    leases = [public_vms_lease(l) for l in await store.list_leases()]
    return {
        "ha_enabled": vms_ha_enabled(),
        "local_vms_server_id": local_vms_server_id(),
        "local_role": local_vms_role(),
        "local_is_leader": await local_is_coordinator(),
        "heartbeat_timeout_seconds": vms_heartbeat_timeout_seconds(),
        "leader_ttl_seconds": vms_leader_ttl_seconds(),
        "nodes": nodes,
        "leases": leases,
        "rdso_18_1_29": True,
        "recording_ha_separate": True,
        "sessions_shared_via_mongodb": True,
    }


async def list_vms_nodes_public(
    *, healthy_only: bool = False, limit: int | None = None
) -> dict[str, Any]:
    await refresh_node_health_flags()
    store = get_vms_ha_store()
    items = []
    for doc in await store.list_nodes():
        pub = public_vms_node(doc)
        if not pub:
            continue
        if healthy_only and not pub.get("healthy"):
            continue
        items.append(pub)
    items.sort(key=lambda n: str(n.get("vms_server_id") or ""))
    total = len(items)
    if limit is not None:
        items = items[: max(1, int(limit))]
    return {
        "items": items,
        "total": total,
        "returned": len(items),
        "ha_enabled": vms_ha_enabled(),
        "local_vms_server_id": local_vms_server_id(),
        "rdso_18_1_29": True,
    }


async def start_vms_ha_loops(
    *,
    start_singletons: StartSingletons | None = None,
    stop_singletons: StopSingletons | None = None,
) -> None:
    """Register local node; when HA enabled, run heartbeat + leader election loops."""
    global _heartbeat_task, _leader_task, _start_singletons, _stop_singletons
    _start_singletons = start_singletons
    _stop_singletons = stop_singletons

    store = get_vms_ha_store()
    ensure = getattr(store, "ensure_indexes", None)
    if ensure:
        try:
            await ensure()
        except Exception as exc:
            logger.warning("[VMS-HA] index setup: %s", exc)

    await heartbeat_local_node(is_leader=not vms_ha_enabled())

    if not vms_ha_enabled():
        # Single-node mode: run singletons immediately (legacy behaviour).
        await _apply_leadership(True)
        return

    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop(), name="vms-ha-heartbeat")
    if _leader_task is None or _leader_task.done():
        _leader_task = asyncio.create_task(_leader_loop(), name="vms-ha-leader")


async def stop_vms_ha_loops() -> None:
    global _heartbeat_task, _leader_task, _local_is_leader, _local_lease_token, _singletons_running
    for task in (_leader_task, _heartbeat_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
    _leader_task = None
    _heartbeat_task = None

    if _local_is_leader and _local_lease_token:
        try:
            store = get_vms_ha_store()
            await store.release_lease(
                lease_name=LEASE_VMS_COORDINATOR,
                owner_vms_server_id=local_vms_server_id(),
                lease_token=_local_lease_token,
            )
        except Exception:
            pass
    if _singletons_running and _stop_singletons:
        try:
            await _stop_singletons()
        except Exception:
            pass
    _singletons_running = False
    _local_is_leader = False
    _local_lease_token = None


def reset_vms_ha_runtime() -> None:
    """Test helper — clear module leadership / task state without awaiting loops."""
    global _heartbeat_task, _leader_task, _start_singletons, _stop_singletons
    global _local_is_leader, _local_lease_token, _singletons_running
    _heartbeat_task = None
    _leader_task = None
    _start_singletons = None
    _stop_singletons = None
    _local_is_leader = False
    _local_lease_token = None
    _singletons_running = False
