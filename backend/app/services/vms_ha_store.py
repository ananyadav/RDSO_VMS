"""RDSO 18.1.29 — VMS node registry + singleton leader leases (Mongo + in-memory tests)."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


class InMemoryVmsHaStore:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.leases: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.nodes.clear()
        self.leases.clear()

    async def ensure_indexes(self) -> None:
        return None

    async def upsert_node(self, vms_server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        cur = dict(self.nodes.get(vms_server_id) or {"vms_server_id": vms_server_id})
        cur.update(patch)
        cur["vms_server_id"] = vms_server_id
        self.nodes[vms_server_id] = cur
        return copy.deepcopy(cur)

    async def get_node(self, vms_server_id: str) -> Optional[dict[str, Any]]:
        doc = self.nodes.get(vms_server_id)
        return copy.deepcopy(doc) if doc else None

    async def list_nodes(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(v) for v in self.nodes.values()]

    async def get_lease(self, lease_name: str) -> Optional[dict[str, Any]]:
        doc = self.leases.get(lease_name)
        return copy.deepcopy(doc) if doc else None

    async def list_leases(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(v) for v in self.leases.values()]

    async def try_claim_lease(
        self,
        *,
        lease_name: str,
        owner_vms_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
    ) -> Optional[dict[str, Any]]:
        now = _iso()
        cur = self.leases.get(lease_name)
        if cur and cur.get("owner_vms_server_id") == owner_vms_server_id:
            cur.update(
                {
                    "lease_token": lease_token,
                    "expires_at": expires_at,
                    "heartbeat_at": heartbeat_at,
                    "updated_at": now,
                }
            )
            self.leases[lease_name] = cur
            return copy.deepcopy(cur)
        if cur:
            owner = cur.get("owner_vms_server_id")
            expired = str(cur.get("expires_at") or "") <= now
            if owner and not expired:
                return None
        doc = {
            "lease_name": lease_name,
            "owner_vms_server_id": owner_vms_server_id,
            "lease_token": lease_token,
            "expires_at": expires_at,
            "heartbeat_at": heartbeat_at,
            "updated_at": now,
        }
        self.leases[lease_name] = doc
        return copy.deepcopy(doc)

    async def renew_lease(
        self,
        *,
        lease_name: str,
        owner_vms_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
    ) -> Optional[dict[str, Any]]:
        cur = self.leases.get(lease_name)
        if not cur:
            return None
        if cur.get("owner_vms_server_id") != owner_vms_server_id:
            return None
        if lease_token and cur.get("lease_token") not in (None, "", lease_token):
            # Allow renew with same owner even if token rotated by claim
            pass
        cur.update(
            {
                "lease_token": lease_token or cur.get("lease_token"),
                "expires_at": expires_at,
                "heartbeat_at": heartbeat_at,
                "updated_at": _iso(),
            }
        )
        self.leases[lease_name] = cur
        return copy.deepcopy(cur)

    async def release_lease(
        self, *, lease_name: str, owner_vms_server_id: str, lease_token: str | None = None
    ) -> bool:
        cur = self.leases.get(lease_name)
        if not cur:
            return False
        if cur.get("owner_vms_server_id") != owner_vms_server_id:
            return False
        if lease_token and cur.get("lease_token") not in (None, "", lease_token):
            return False
        del self.leases[lease_name]
        return True


class MongoVmsHaStore:
    def __init__(self) -> None:
        from app.core.database import database

        self.nodes = database.get_collection("vms_servers")
        self.leases = database.get_collection("vms_leases")

    async def ensure_indexes(self) -> None:
        await self.nodes.create_index("vms_server_id", unique=True, name="idx_vms_server_id")
        await self.nodes.create_index("last_seen", name="idx_vms_last_seen")
        await self.leases.create_index("lease_name", unique=True, name="idx_vms_lease_name")
        await self.leases.create_index("expires_at", name="idx_vms_lease_expires")

    async def upsert_node(self, vms_server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        await self.nodes.update_one(
            {"vms_server_id": vms_server_id},
            {"$set": {**patch, "vms_server_id": vms_server_id}},
            upsert=True,
        )
        return await self.get_node(vms_server_id) or {"vms_server_id": vms_server_id, **patch}

    async def get_node(self, vms_server_id: str) -> Optional[dict[str, Any]]:
        return await self.nodes.find_one({"vms_server_id": vms_server_id})

    async def list_nodes(self) -> list[dict[str, Any]]:
        return [doc async for doc in self.nodes.find({})]

    async def get_lease(self, lease_name: str) -> Optional[dict[str, Any]]:
        return await self.leases.find_one({"lease_name": lease_name})

    async def list_leases(self) -> list[dict[str, Any]]:
        return [doc async for doc in self.leases.find({})]

    async def try_claim_lease(
        self,
        *,
        lease_name: str,
        owner_vms_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
    ) -> Optional[dict[str, Any]]:
        now = _iso()
        renewed = await self.leases.find_one_and_update(
            {"lease_name": lease_name, "owner_vms_server_id": owner_vms_server_id},
            {
                "$set": {
                    "lease_token": lease_token,
                    "expires_at": expires_at,
                    "heartbeat_at": heartbeat_at,
                    "updated_at": now,
                }
            },
            return_document=True,
        )
        if renewed:
            return renewed

        # Insert if missing or expired
        existing = await self.get_lease(lease_name)
        if existing:
            owner = existing.get("owner_vms_server_id")
            expired = str(existing.get("expires_at") or "") <= now
            if owner and not expired:
                return None
            stolen = await self.leases.find_one_and_update(
                {
                    "lease_name": lease_name,
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"owner_vms_server_id": {"$in": [None, ""]}},
                    ],
                },
                {
                    "$set": {
                        "owner_vms_server_id": owner_vms_server_id,
                        "lease_token": lease_token,
                        "expires_at": expires_at,
                        "heartbeat_at": heartbeat_at,
                        "updated_at": now,
                    }
                },
                return_document=True,
            )
            return stolen

        try:
            await self.leases.insert_one(
                {
                    "lease_name": lease_name,
                    "owner_vms_server_id": owner_vms_server_id,
                    "lease_token": lease_token,
                    "expires_at": expires_at,
                    "heartbeat_at": heartbeat_at,
                    "updated_at": now,
                }
            )
        except Exception:
            # Race — another node inserted
            return None
        return await self.get_lease(lease_name)

    async def renew_lease(
        self,
        *,
        lease_name: str,
        owner_vms_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
    ) -> Optional[dict[str, Any]]:
        return await self.leases.find_one_and_update(
            {"lease_name": lease_name, "owner_vms_server_id": owner_vms_server_id},
            {
                "$set": {
                    "lease_token": lease_token,
                    "expires_at": expires_at,
                    "heartbeat_at": heartbeat_at,
                    "updated_at": _iso(),
                }
            },
            return_document=True,
        )

    async def release_lease(
        self, *, lease_name: str, owner_vms_server_id: str, lease_token: str | None = None
    ) -> bool:
        filt: dict[str, Any] = {
            "lease_name": lease_name,
            "owner_vms_server_id": owner_vms_server_id,
        }
        if lease_token:
            filt["lease_token"] = lease_token
        result = await self.leases.delete_one(filt)
        return bool(result.deleted_count)


_STORE: InMemoryVmsHaStore | MongoVmsHaStore | None = None
_FORCE_MEMORY = False


def use_memory_vms_ha_store(store: InMemoryVmsHaStore | None = None) -> InMemoryVmsHaStore:
    global _STORE, _FORCE_MEMORY
    _FORCE_MEMORY = True
    _STORE = store or InMemoryVmsHaStore()
    return _STORE  # type: ignore[return-value]


def reset_vms_ha_store() -> None:
    global _STORE, _FORCE_MEMORY
    _STORE = None
    _FORCE_MEMORY = False


def get_vms_ha_store() -> InMemoryVmsHaStore | MongoVmsHaStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    if _FORCE_MEMORY:
        _STORE = InMemoryVmsHaStore()
        return _STORE
    try:
        _STORE = MongoVmsHaStore()
    except Exception:
        logger.warning("[VMS-HA] Mongo unavailable — using in-memory store")
        _STORE = InMemoryVmsHaStore()
    return _STORE
