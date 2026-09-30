"""RDSO 18.3.3 — recording HA persistence (Mongo + in-memory for tests)."""

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


class InMemoryHaStore:
    """Logical multi-server store for unit tests — not a fake physical fleet."""

    def __init__(self) -> None:
        self.servers: dict[str, dict[str, Any]] = {}
        self.ownership: dict[str, dict[str, Any]] = {}  # camera_id -> doc
        self.cameras: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.servers.clear()
        self.ownership.clear()
        self.cameras.clear()

    async def upsert_server(self, server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        cur = dict(self.servers.get(server_id) or {"server_id": server_id})
        cur.update(patch)
        cur["server_id"] = server_id
        self.servers[server_id] = cur
        return copy.deepcopy(cur)

    async def get_server(self, server_id: str) -> Optional[dict[str, Any]]:
        doc = self.servers.get(server_id)
        return copy.deepcopy(doc) if doc else None

    async def list_servers(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(v) for v in self.servers.values()]

    async def set_camera_home(self, camera_id: str, home_server_id: str) -> None:
        cam = dict(self.cameras.get(camera_id) or {"_id": camera_id, "id": camera_id})
        cam["recording_server_id"] = home_server_id
        self.cameras[camera_id] = cam

    async def get_camera(self, camera_id: str) -> Optional[dict[str, Any]]:
        doc = self.cameras.get(camera_id)
        return copy.deepcopy(doc) if doc else None

    async def list_cameras_for_home(self, home_server_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(c)
            for c in self.cameras.values()
            if (c.get("recording_server_id") or "") == home_server_id
        ]

    async def list_all_assigned_cameras(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(c)
            for c in self.cameras.values()
            if c.get("recording_server_id")
        ]

    async def get_ownership(self, camera_id: str) -> Optional[dict[str, Any]]:
        doc = self.ownership.get(camera_id)
        return copy.deepcopy(doc) if doc else None

    async def list_ownership(self, *, owner_server_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        for doc in self.ownership.values():
            if owner_server_id and doc.get("owner_server_id") != owner_server_id:
                continue
            out.append(copy.deepcopy(doc))
        return out

    async def try_claim_ownership(
        self,
        *,
        camera_id: str,
        owner_server_id: str,
        home_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
        failover: bool = False,
        allow_steal_expired: bool = True,
    ) -> Optional[dict[str, Any]]:
        now = _iso()
        cur = self.ownership.get(camera_id)
        if cur:
            owner = cur.get("owner_server_id")
            expired = str(cur.get("expires_at") or "") <= now
            if owner == owner_server_id:
                cur.update(
                    {
                        "lease_token": lease_token,
                        "expires_at": expires_at,
                        "heartbeat_at": heartbeat_at,
                        "updated_at": now,
                        "home_server_id": home_server_id or cur.get("home_server_id"),
                        "failover": bool(failover) if failover else cur.get("failover", False),
                    }
                )
                self.ownership[camera_id] = cur
                return copy.deepcopy(cur)
            if owner and not expired:
                return None
            if owner and expired and not allow_steal_expired:
                return None
        doc = {
            "camera_id": camera_id,
            "home_server_id": home_server_id,
            "owner_server_id": owner_server_id,
            "lease_token": lease_token,
            "state": "active",
            "heartbeat_at": heartbeat_at,
            "expires_at": expires_at,
            "recording_active": False,
            "failover": bool(failover),
            "failback_requested": False,
            "updated_at": now,
        }
        self.ownership[camera_id] = doc
        return copy.deepcopy(doc)

    async def renew_ownership(
        self,
        camera_id: str,
        *,
        owner_server_id: str,
        lease_token: str | None,
        expires_at: str,
        heartbeat_at: str,
        recording_active: bool | None = None,
    ) -> bool:
        cur = self.ownership.get(camera_id)
        if not cur or cur.get("owner_server_id") != owner_server_id:
            return False
        if lease_token and cur.get("lease_token") != lease_token:
            return False
        cur["expires_at"] = expires_at
        cur["heartbeat_at"] = heartbeat_at
        cur["updated_at"] = _iso()
        if recording_active is not None:
            cur["recording_active"] = bool(recording_active)
        self.ownership[camera_id] = cur
        return True

    async def release_ownership(
        self,
        camera_id: str,
        *,
        owner_server_id: str,
        reason: str = "",
    ) -> bool:
        cur = self.ownership.get(camera_id)
        if not cur or cur.get("owner_server_id") != owner_server_id:
            return False
        self.ownership.pop(camera_id, None)
        return True

    async def patch_ownership(self, camera_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
        cur = self.ownership.get(camera_id)
        if not cur:
            return None
        cur.update(patch)
        cur["updated_at"] = _iso()
        self.ownership[camera_id] = cur
        return copy.deepcopy(cur)


class MongoHaStore:
    """Production store backed by MongoDB collections."""

    def __init__(self) -> None:
        from app.core.database import database

        self.servers = database.get_collection("recording_servers")
        self.ownership = database.get_collection("recording_ownership")
        self.cameras = database.get_collection("cameras")

    async def ensure_indexes(self) -> None:
        await self.servers.create_index("server_id", unique=True, name="idx_recording_server_id")
        await self.ownership.create_index("camera_id", unique=True, name="idx_recording_ownership_camera")
        await self.ownership.create_index("owner_server_id", name="idx_recording_ownership_owner")
        await self.cameras.create_index("recording_server_id", name="idx_camera_recording_server_id")

    async def upsert_server(self, server_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        await self.servers.update_one(
            {"server_id": server_id},
            {"$set": {**patch, "server_id": server_id}},
            upsert=True,
        )
        return await self.get_server(server_id) or {"server_id": server_id, **patch}

    async def get_server(self, server_id: str) -> Optional[dict[str, Any]]:
        return await self.servers.find_one({"server_id": server_id})

    async def list_servers(self) -> list[dict[str, Any]]:
        return [doc async for doc in self.servers.find({})]

    async def set_camera_home(self, camera_id: str, home_server_id: str) -> None:
        from bson import ObjectId

        if not ObjectId.is_valid(camera_id):
            return
        await self.cameras.update_one(
            {"_id": ObjectId(camera_id)},
            {"$set": {"recording_server_id": home_server_id}},
        )

    async def get_camera(self, camera_id: str) -> Optional[dict[str, Any]]:
        from bson import ObjectId

        if not ObjectId.is_valid(camera_id):
            return None
        doc = await self.cameras.find_one({"_id": ObjectId(camera_id)})
        if doc:
            doc = dict(doc)
            doc["id"] = str(doc["_id"])
        return doc

    async def list_cameras_for_home(self, home_server_id: str) -> list[dict[str, Any]]:
        out = []
        async for doc in self.cameras.find({"recording_server_id": home_server_id}):
            d = dict(doc)
            d["id"] = str(d["_id"])
            out.append(d)
        return out

    async def list_all_assigned_cameras(self) -> list[dict[str, Any]]:
        out = []
        async for doc in self.cameras.find(
            {"recording_server_id": {"$exists": True, "$nin": [None, ""]}}
        ):
            d = dict(doc)
            d["id"] = str(d["_id"])
            out.append(d)
        return out

    async def get_ownership(self, camera_id: str) -> Optional[dict[str, Any]]:
        return await self.ownership.find_one({"camera_id": camera_id})

    async def list_ownership(self, *, owner_server_id: str | None = None) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if owner_server_id:
            q["owner_server_id"] = owner_server_id
        return [doc async for doc in self.ownership.find(q)]

    async def try_claim_ownership(
        self,
        *,
        camera_id: str,
        owner_server_id: str,
        home_server_id: str,
        lease_token: str,
        expires_at: str,
        heartbeat_at: str,
        failover: bool = False,
        allow_steal_expired: bool = True,
    ) -> Optional[dict[str, Any]]:
        now = _iso()
        # Renew if we already own
        renewed = await self.ownership.find_one_and_update(
            {"camera_id": camera_id, "owner_server_id": owner_server_id},
            {
                "$set": {
                    "lease_token": lease_token,
                    "expires_at": expires_at,
                    "heartbeat_at": heartbeat_at,
                    "updated_at": now,
                    "home_server_id": home_server_id,
                }
            },
            return_document=True,
        )
        if renewed:
            return renewed

        filt: dict[str, Any] = {
            "camera_id": camera_id,
            "$or": [
                {"owner_server_id": {"$exists": False}},
                {"owner_server_id": None},
                {"owner_server_id": ""},
            ],
        }
        if allow_steal_expired:
            filt = {
                "camera_id": camera_id,
                "$or": [
                    {"owner_server_id": {"$exists": False}},
                    {"owner_server_id": None},
                    {"owner_server_id": ""},
                    {"expires_at": {"$lte": now}},
                ],
            }

        doc = {
            "camera_id": camera_id,
            "home_server_id": home_server_id,
            "owner_server_id": owner_server_id,
            "lease_token": lease_token,
            "state": "active",
            "heartbeat_at": heartbeat_at,
            "expires_at": expires_at,
            "recording_active": False,
            "failover": bool(failover),
            "failback_requested": False,
            "updated_at": now,
        }
        # Upsert only when missing or expired — use update with filter
        existing = await self.ownership.find_one({"camera_id": camera_id})
        if existing:
            owner = existing.get("owner_server_id")
            expired = str(existing.get("expires_at") or "") <= now
            if owner and owner != owner_server_id and not expired:
                return None
            await self.ownership.update_one({"camera_id": camera_id}, {"$set": doc})
            return await self.get_ownership(camera_id)

        try:
            await self.ownership.insert_one(doc)
        except Exception:
            # Race: another server inserted first
            existing = await self.get_ownership(camera_id)
            if existing and existing.get("owner_server_id") == owner_server_id:
                return existing
            return None
        return await self.get_ownership(camera_id)

    async def renew_ownership(
        self,
        camera_id: str,
        *,
        owner_server_id: str,
        lease_token: str | None,
        expires_at: str,
        heartbeat_at: str,
        recording_active: bool | None = None,
    ) -> bool:
        filt: dict[str, Any] = {"camera_id": camera_id, "owner_server_id": owner_server_id}
        if lease_token:
            filt["lease_token"] = lease_token
        patch: dict[str, Any] = {
            "expires_at": expires_at,
            "heartbeat_at": heartbeat_at,
            "updated_at": _iso(),
        }
        if recording_active is not None:
            patch["recording_active"] = bool(recording_active)
        res = await self.ownership.update_one(filt, {"$set": patch})
        return res.matched_count > 0

    async def release_ownership(
        self,
        camera_id: str,
        *,
        owner_server_id: str,
        reason: str = "",
    ) -> bool:
        res = await self.ownership.delete_one(
            {"camera_id": camera_id, "owner_server_id": owner_server_id}
        )
        if res.deleted_count and reason:
            logger.info(
                "[HA] Released ownership camera=%s server=%s reason=%s",
                camera_id,
                owner_server_id,
                reason,
            )
        return res.deleted_count > 0

    async def patch_ownership(self, camera_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
        await self.ownership.update_one(
            {"camera_id": camera_id},
            {"$set": {**patch, "updated_at": _iso()}},
        )
        return await self.get_ownership(camera_id)


_STORE: InMemoryHaStore | MongoHaStore | None = None
_FORCE_MEMORY = False


def use_memory_ha_store(store: InMemoryHaStore | None = None) -> InMemoryHaStore:
    """Test helper: force in-memory logical servers."""
    global _STORE, _FORCE_MEMORY
    _FORCE_MEMORY = True
    _STORE = store or InMemoryHaStore()
    return _STORE  # type: ignore[return-value]


def reset_ha_store() -> None:
    global _STORE, _FORCE_MEMORY
    _STORE = None
    _FORCE_MEMORY = False


def get_ha_store() -> InMemoryHaStore | MongoHaStore:
    global _STORE
    if _STORE is not None:
        return _STORE
    if _FORCE_MEMORY:
        _STORE = InMemoryHaStore()
        return _STORE
    try:
        _STORE = MongoHaStore()
    except Exception:
        logger.warning("[HA] Mongo unavailable — using in-memory store")
        _STORE = InMemoryHaStore()
    return _STORE
