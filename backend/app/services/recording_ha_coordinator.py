"""RDSO 18.3.3 — N:1 recording-server redundancy coordinator."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.recording_ha_store import get_ha_store
from app.services.recording_ha_types import (
    OWNERSHIP_ACTIVE,
    OWNERSHIP_PENDING_FAILBACK,
    ROLE_PRIMARY,
    ROLE_STANDBY,
    public_ownership,
    public_server,
)
from app.services.recording_server_config import (
    heartbeat_interval_seconds,
    heartbeat_timeout_seconds,
    local_recording_server_id,
    local_server_role,
    ownership_ttl_seconds,
    recording_ha_enabled,
)

logger = logging.getLogger(__name__)

_heartbeat_task: asyncio.Task | None = None
_failover_task: asyncio.Task | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_server_fresh(server: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    """Bounded heartbeat timeout — True when last_seen is within timeout window."""
    if not server or not server.get("enabled", True):
        return False
    last = _parse_iso(server.get("last_seen"))
    if last is None:
        return False
    now = now or _utcnow()
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (now - last).total_seconds()
    return age <= heartbeat_timeout_seconds()


async def register_local_server(*, hostname: str = "") -> dict[str, Any]:
    store = get_ha_store()
    server_id = local_recording_server_id()
    role = local_server_role()
    now = _iso()
    doc = await store.upsert_server(
        server_id,
        {
            "role": role,
            "enabled": True,
            "healthy": True,
            "last_seen": now,
            "hostname": hostname or server_id,
            "protects_primary_ids": [],  # empty standby = protects all primaries (N:1)
            "metadata": {"ha_enabled": recording_ha_enabled()},
        },
    )
    return public_server(doc) or {"server_id": server_id}


async def heartbeat_local_server(*, active_ownership_count: int | None = None) -> dict[str, Any]:
    store = get_ha_store()
    server_id = local_recording_server_id()
    now = _iso()
    patch: dict[str, Any] = {
        "healthy": True,
        "last_seen": now,
        "enabled": True,
        "role": local_server_role(),
    }
    if active_ownership_count is not None:
        patch["active_ownership_count"] = int(active_ownership_count)
    doc = await store.upsert_server(server_id, patch)
    # Mark other servers' health based on last_seen
    await refresh_server_health_flags()
    return public_server(doc) or {"server_id": server_id}


async def refresh_server_health_flags() -> list[dict[str, Any]]:
    store = get_ha_store()
    now = _utcnow()
    out = []
    for doc in await store.list_servers():
        fresh = is_server_fresh(doc, now=now)
        if bool(doc.get("healthy")) != fresh:
            doc = await store.upsert_server(doc["server_id"], {"healthy": fresh})
        out.append(public_server(doc) or doc)
    return out


async def assign_camera_home(camera_id: str, home_server_id: str) -> dict[str, Any]:
    store = get_ha_store()
    await store.set_camera_home(camera_id, home_server_id)
    return {"ok": True, "camera_id": camera_id, "recording_server_id": home_server_id}


def _camera_id(cam: dict[str, Any]) -> str:
    return str(cam.get("id") or cam.get("_id") or cam.get("camera_id") or "")


async def claim_camera_ownership(
    camera_id: str,
    *,
    server_id: str | None = None,
    home_server_id: str | None = None,
    failover: bool = False,
) -> dict[str, Any]:
    """Acquire exclusive ownership. Exactly one owner per camera."""
    store = get_ha_store()
    server_id = server_id or local_recording_server_id()
    cam = await store.get_camera(camera_id)
    home = home_server_id or (cam or {}).get("recording_server_id") or server_id
    now = _utcnow()
    expires = now + timedelta(seconds=ownership_ttl_seconds())
    token = uuid.uuid4().hex
    claimed = await store.try_claim_ownership(
        camera_id=camera_id,
        owner_server_id=server_id,
        home_server_id=str(home),
        lease_token=token,
        expires_at=_iso(expires),
        heartbeat_at=_iso(now),
        failover=failover,
        allow_steal_expired=True,
    )
    if not claimed:
        existing = await store.get_ownership(camera_id)
        return {
            "ok": False,
            "reason": "owned_by_other",
            "ownership": public_ownership(existing),
        }
    return {"ok": True, "ownership": public_ownership(claimed)}


async def renew_local_ownership(camera_id: str, *, recording_active: bool | None = None) -> bool:
    store = get_ha_store()
    server_id = local_recording_server_id()
    own = await store.get_ownership(camera_id)
    if not own or own.get("owner_server_id") != server_id:
        return False
    now = _utcnow()
    return await store.renew_ownership(
        camera_id,
        owner_server_id=server_id,
        lease_token=own.get("lease_token"),
        expires_at=_iso(now + timedelta(seconds=ownership_ttl_seconds())),
        heartbeat_at=_iso(now),
        recording_active=recording_active,
    )


async def release_camera_ownership(
    camera_id: str,
    *,
    server_id: str | None = None,
    reason: str = "",
) -> bool:
    store = get_ha_store()
    server_id = server_id or local_recording_server_id()
    return await store.release_ownership(camera_id, owner_server_id=server_id, reason=reason)


async def set_recording_active_flag(camera_id: str, active: bool) -> None:
    store = get_ha_store()
    server_id = local_recording_server_id()
    own = await store.get_ownership(camera_id)
    if not own or own.get("owner_server_id") != server_id:
        return
    await store.renew_ownership(
        camera_id,
        owner_server_id=server_id,
        lease_token=own.get("lease_token"),
        expires_at=own.get("expires_at") or _iso(_utcnow() + timedelta(seconds=ownership_ttl_seconds())),
        heartbeat_at=_iso(),
        recording_active=active,
    )


async def local_may_record_camera(camera_id: str) -> dict[str, Any]:
    """Gate for start_camera_recording — prevents split-brain duplicate recording."""
    if not recording_ha_enabled():
        # Single-server mode: always allow; ownership stamp is best-effort only
        try:
            claimed = await claim_camera_ownership(camera_id, failover=False)
            return {"allowed": True, "mode": "single", **claimed}
        except Exception as exc:
            logger.debug("[HA] Single-server ownership stamp skipped: %s", exc)
            return {"allowed": True, "mode": "single", "ok": True, "warning": str(exc)}

    server_id = local_recording_server_id()
    store = get_ha_store()
    own = await store.get_ownership(camera_id)
    now = _iso()
    if own and own.get("owner_server_id") == server_id:
        # renew
        await renew_local_ownership(camera_id)
        return {"allowed": True, "mode": "ha", "ownership": public_ownership(own)}

    if own and own.get("owner_server_id") and str(own.get("expires_at") or "") > now:
        return {
            "allowed": False,
            "mode": "ha",
            "reason": "owned_by_other",
            "ownership": public_ownership(own),
        }

    claimed = await claim_camera_ownership(camera_id)
    return {"allowed": bool(claimed.get("ok")), "mode": "ha", **claimed}


def standby_protects_primary(standby: dict[str, Any], primary_id: str) -> bool:
    protects = list(standby.get("protects_primary_ids") or [])
    if not protects:
        return True  # N:1 default: one standby covers all primaries
    return primary_id in protects


async def detect_failed_primaries() -> list[dict[str, Any]]:
    await refresh_server_health_flags()
    store = get_ha_store()
    failed = []
    for doc in await store.list_servers():
        if (doc.get("role") or ROLE_PRIMARY) != ROLE_PRIMARY:
            continue
        if not doc.get("enabled", True):
            continue
        if is_server_fresh(doc):
            continue
        failed.append(public_server(doc) or doc)
    return failed


async def standby_takeover_for_primary(
    primary_id: str,
    *,
    standby_id: str | None = None,
) -> dict[str, Any]:
    """Move eligible cameras from a failed primary to the standby (ownership only)."""
    store = get_ha_store()
    standby_id = standby_id or local_recording_server_id()
    standby = await store.get_server(standby_id)
    if not standby or (standby.get("role") or "") != ROLE_STANDBY:
        return {"ok": False, "error": "standby_unavailable", "taken": []}
    if not standby.get("enabled", True) or not is_server_fresh(standby):
        # During takeover the standby itself must be healthy (just heartbeated)
        if not standby.get("enabled", True):
            return {"ok": False, "error": "standby_unavailable", "taken": []}
    if not standby_protects_primary(standby, primary_id):
        return {"ok": False, "error": "primary_not_protected", "taken": []}

    primary = await store.get_server(primary_id)
    if primary and is_server_fresh(primary):
        return {"ok": False, "error": "primary_still_healthy", "taken": []}

    cameras = await store.list_cameras_for_home(primary_id)
    taken: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cam in cameras:
        cid = _camera_id(cam)
        if not cid:
            continue
        result = await claim_camera_ownership(
            cid,
            server_id=standby_id,
            home_server_id=primary_id,
            failover=True,
        )
        if result.get("ok"):
            taken.append({"camera_id": cid, "ownership": result.get("ownership")})
        else:
            skipped.append({"camera_id": cid, "reason": result.get("reason"), "ownership": result.get("ownership")})

    return {
        "ok": True,
        "primary_id": primary_id,
        "standby_id": standby_id,
        "taken": taken,
        "skipped": skipped,
        "taken_count": len(taken),
    }


async def run_failover_pass(*, standby_id: str | None = None) -> dict[str, Any]:
    """Standby evaluates failed primaries and takes over (N:1)."""
    standby_id = standby_id or local_recording_server_id()
    store = get_ha_store()
    standby = await store.get_server(standby_id)
    if not standby or (standby.get("role") or "") != ROLE_STANDBY:
        return {"ok": False, "error": "not_standby", "results": []}

    failed = await detect_failed_primaries()
    results = []
    for primary in failed:
        pid = primary.get("server_id")
        if not pid:
            continue
        results.append(await standby_takeover_for_primary(pid, standby_id=standby_id))
    return {"ok": True, "failed_primaries": failed, "results": results}


async def request_failback(
    primary_id: str,
    *,
    camera_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Mark ownership for controlled failback — does not steal active recordings."""
    store = get_ha_store()
    primary = await store.get_server(primary_id)
    if not primary or not is_server_fresh(primary):
        return {"ok": False, "error": "primary_not_healthy"}

    targets = camera_ids
    if not targets:
        cams = await store.list_cameras_for_home(primary_id)
        targets = [_camera_id(c) for c in cams if _camera_id(c)]

    marked = []
    for cid in targets:
        own = await store.get_ownership(cid)
        if not own:
            continue
        if own.get("home_server_id") != primary_id:
            continue
        if own.get("owner_server_id") == primary_id:
            continue
        await store.patch_ownership(
            cid,
            {
                "failback_requested": True,
                "state": OWNERSHIP_PENDING_FAILBACK,
            },
        )
        marked.append(cid)
    return {"ok": True, "primary_id": primary_id, "marked": marked}


async def process_failback_releases(
    *,
    owner_server_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Owner (usually standby) releases cameras marked for failback when safe."""
    store = get_ha_store()
    owner_server_id = owner_server_id or local_recording_server_id()
    released = []
    held = []
    for own in await store.list_ownership(owner_server_id=owner_server_id):
        if not own.get("failback_requested"):
            continue
        cid = own.get("camera_id")
        if not cid:
            continue
        if own.get("recording_active") and not force:
            held.append({"camera_id": cid, "reason": "recording_active"})
            continue
        ok = await store.release_ownership(
            cid, owner_server_id=owner_server_id, reason="controlled_failback"
        )
        if ok:
            released.append(cid)
    return {"ok": True, "released": released, "held": held, "force": force}


async def reclaim_after_failback(primary_id: str | None = None) -> dict[str, Any]:
    """Primary reclaims home cameras that no longer have an owner."""
    store = get_ha_store()
    primary_id = primary_id or local_recording_server_id()
    reclaimed = []
    for cam in await store.list_cameras_for_home(primary_id):
        cid = _camera_id(cam)
        if not cid:
            continue
        own = await store.get_ownership(cid)
        if own and own.get("owner_server_id") and str(own.get("expires_at") or "") > _iso():
            continue
        result = await claim_camera_ownership(
            cid, server_id=primary_id, home_server_id=primary_id, failover=False
        )
        if result.get("ok"):
            # clear failover flag
            await store.patch_ownership(
                cid,
                {
                    "failover": False,
                    "failback_requested": False,
                    "state": OWNERSHIP_ACTIVE,
                },
            )
            reclaimed.append(cid)
    return {"ok": True, "primary_id": primary_id, "reclaimed": reclaimed}


async def ha_status_summary() -> dict[str, Any]:
    await refresh_server_health_flags()
    store = get_ha_store()
    servers = [public_server(s) for s in await store.list_servers()]
    ownership = [public_ownership(o) for o in await store.list_ownership()]
    return {
        "ha_enabled": recording_ha_enabled(),
        "local_server_id": local_recording_server_id(),
        "local_role": local_server_role(),
        "heartbeat_timeout_seconds": heartbeat_timeout_seconds(),
        "ownership_ttl_seconds": ownership_ttl_seconds(),
        "servers": servers,
        "ownership": ownership,
        "rdso_18_3_3": True,
    }


async def ensure_session_server_fields(session_updates: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fields stamped onto recording sessions."""
    patch = {
        "recording_server_id": local_recording_server_id(),
        "recording_server_role": local_server_role(),
    }
    if session_updates:
        patch.update(session_updates)
    return patch


async def _heartbeat_loop() -> None:
    while True:
        try:
            store = get_ha_store()
            owned = await store.list_ownership(owner_server_id=local_recording_server_id())
            await heartbeat_local_server(active_ownership_count=len(owned))
            for own in owned:
                cid = own.get("camera_id")
                if cid:
                    await renew_local_ownership(cid, recording_active=own.get("recording_active"))
            # Controlled failback releases when safe
            await process_failback_releases(force=False)
            if local_server_role() == ROLE_PRIMARY:
                await reclaim_after_failback()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[HA] Heartbeat loop error: %s", exc)
        await asyncio.sleep(heartbeat_interval_seconds())


async def _failover_loop() -> None:
    while True:
        try:
            if recording_ha_enabled() and local_server_role() == ROLE_STANDBY:
                await run_failover_pass()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[HA] Failover loop error: %s", exc)
        await asyncio.sleep(max(2.0, heartbeat_interval_seconds()))


async def start_recording_ha_loops() -> None:
    """Register local server and start heartbeat (+ failover if standby/HA)."""
    global _heartbeat_task, _failover_task
    await register_local_server()
    await heartbeat_local_server(active_ownership_count=0)
    try:
        store = get_ha_store()
        ensure = getattr(store, "ensure_indexes", None)
        if callable(ensure):
            await ensure()
    except Exception as exc:
        logger.debug("[HA] Index ensure skipped: %s", exc)

    if _heartbeat_task is None or _heartbeat_task.done():
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    if recording_ha_enabled() and (_failover_task is None or _failover_task.done()):
        _failover_task = asyncio.create_task(_failover_loop())
    logger.info(
        "[HA] Recording server %s role=%s ha_enabled=%s",
        local_recording_server_id(),
        local_server_role(),
        recording_ha_enabled(),
    )


async def stop_recording_ha_loops() -> None:
    global _heartbeat_task, _failover_task
    for task in (_heartbeat_task, _failover_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _heartbeat_task = None
    _failover_task = None
