"""RDSO 18.6.22.3 / 18.6.22.12 — CCC centralized device/sensor registry.

Normalized model over real sources (camera, VMS, camera digital input, generic
external sensor). No fake vendor integrations. Does not open media streams on register.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

import bcrypt
from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database
from app.services.audit_service import sanitize_metadata
from app.services.priority_levels import normalize_priority

devices_collection = database.get_collection("ccc_devices")

DEVICE_TYPES = frozenset(
    {
        "camera",
        "vms",
        "camera_digital_input",
        "external_sensor",
    }
)

DEVICE_STATUSES = frozenset({"online", "offline", "unknown", "degraded", "disabled"})

# Soft page cap only — no hard registry size limit.
LIST_PAGE_MAX = 200
INGEST_BODY_MAX_BYTES = 16384
INGEST_METADATA_MAX_KEYS = 32
INGEST_RATE_WINDOW_SECONDS = 10
INGEST_RATE_MAX_PER_WINDOW = 60


class CccDeviceError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if value is None:
        return None
    return str(value)


def hash_integration_secret(raw: str) -> str:
    """Store integration secrets with bcrypt (never return plaintext)."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_integration_secret(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def generate_integration_secret() -> str:
    return secrets.token_urlsafe(32)


def device_to_public(doc: dict, *, include_secret_once: Optional[str] = None) -> dict[str, Any]:
    meta = sanitize_metadata(doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {})
    out: dict[str, Any] = {
        "id": str(doc["_id"]),
        "device_uid": doc.get("device_uid") or str(doc["_id"]),
        "type": doc.get("type") or "external_sensor",
        "name": doc.get("name") or "",
        "location": doc.get("location") or "",
        "enabled": bool(doc.get("enabled", True)),
        "status": doc.get("status") or "unknown",
        "health": doc.get("health") or "unknown",
        "capabilities": list(doc.get("capabilities") or []),
        "last_seen": _iso(doc.get("last_seen")),
        "linked_camera_ids": list(doc.get("linked_camera_ids") or []),
        "linked_vms_source": doc.get("linked_vms_source"),
        "default_priority": int(doc.get("default_priority") or 3),
        "active_alert_count": int(doc.get("active_alert_count") or 0),
        "last_event_id": doc.get("last_event_id"),
        "metadata": meta,
        "has_integration_secret": bool(doc.get("api_key_hash")),
        "opens_streams_on_register": False,
        "fake_vendor": False,
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }
    if include_secret_once:
        out["integration_secret"] = include_secret_once
        out["integration_secret_note"] = "Shown once — store securely; not retrievable later"
    return out


async def ensure_ccc_device_indexes() -> None:
    try:
        await devices_collection.create_index(
            "device_uid", unique=True, name="idx_ccc_device_uid"
        )
        await devices_collection.create_index("type", name="idx_ccc_device_type")
        await devices_collection.create_index("enabled", name="idx_ccc_device_enabled")
        await devices_collection.create_index("status", name="idx_ccc_device_status")
        await devices_collection.create_index("location", name="idx_ccc_device_location")
        await devices_collection.create_index("last_seen", name="idx_ccc_device_last_seen")
        await devices_collection.create_index(
            [("type", 1), ("enabled", 1), ("status", 1)],
            name="idx_ccc_device_type_enabled_status",
        )
    except Exception:
        pass


async def create_device(
    *,
    name: str,
    type: str,
    location: str = "",
    device_uid: Optional[str] = None,
    linked_camera_ids: Optional[list[str]] = None,
    linked_vms_source: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
    metadata: Optional[dict] = None,
    default_priority: Any = 3,
    enabled: bool = True,
    generate_secret: bool = True,
) -> dict[str, Any]:
    name_s = (name or "").strip()
    type_s = (type or "").strip().lower()
    if not name_s:
        raise CccDeviceError("name required")
    if type_s not in DEVICE_TYPES:
        raise CccDeviceError(
            f"Unsupported type (no fake vendors). Allowed: {sorted(DEVICE_TYPES)}"
        )
    uid = (device_uid or "").strip() or f"{type_s}_{secrets.token_hex(6)}"
    now = _utcnow()
    secret_plain: Optional[str] = None
    api_key_hash = None
    if type_s == "external_sensor" and generate_secret:
        secret_plain = generate_integration_secret()
        api_key_hash = hash_integration_secret(secret_plain)

    cams = [str(x).strip() for x in (linked_camera_ids or []) if str(x).strip()]
    if type_s in ("camera", "camera_digital_input") and not cams:
        raise CccDeviceError(f"{type_s} requires linked_camera_ids")

    doc = {
        "device_uid": uid,
        "type": type_s,
        "name": name_s,
        "location": (location or "").strip(),
        "enabled": bool(enabled),
        "status": "unknown",
        "health": "unknown",
        "capabilities": [str(c).strip() for c in (capabilities or []) if str(c).strip()],
        "last_seen": None,
        "linked_camera_ids": cams,
        "linked_vms_source": (linked_vms_source or "local_vms").strip()
        if type_s == "vms"
        else (linked_vms_source or None),
        "default_priority": normalize_priority(default_priority, default=3),
        "active_alert_count": 0,
        "last_event_id": None,
        "metadata": sanitize_metadata(metadata if isinstance(metadata, dict) else {}),
        "api_key_hash": api_key_hash,
        "ingest_window_started": None,
        "ingest_window_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await devices_collection.insert_one(doc)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or getattr(exc, "code", None) == 11000:
            raise CccDeviceError(f"device_uid already exists: {uid}") from exc
        raise
    doc["_id"] = result.inserted_id
    return device_to_public(doc, include_secret_once=secret_plain)


async def update_device(device_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        oid = ObjectId(device_id)
    except (InvalidId, TypeError) as exc:
        raise CccDeviceError("Invalid device id") from exc
    doc = await devices_collection.find_one({"_id": oid})
    if not doc:
        raise CccDeviceError("Device not found")

    updates: dict[str, Any] = {"updated_at": _utcnow()}
    if "name" in patch and patch["name"] is not None:
        name_s = str(patch["name"]).strip()
        if not name_s:
            raise CccDeviceError("name cannot be empty")
        updates["name"] = name_s
    if "location" in patch and patch["location"] is not None:
        updates["location"] = str(patch["location"]).strip()
    if "enabled" in patch and patch["enabled"] is not None:
        updates["enabled"] = bool(patch["enabled"])
        if not updates["enabled"]:
            updates["status"] = "disabled"
    if "status" in patch and patch["status"] is not None:
        st = str(patch["status"]).strip().lower()
        if st not in DEVICE_STATUSES:
            raise CccDeviceError(f"Invalid status: {st}")
        updates["status"] = st
    if "health" in patch and patch["health"] is not None:
        updates["health"] = str(patch["health"]).strip().lower()[:32]
    if "capabilities" in patch and isinstance(patch["capabilities"], list):
        updates["capabilities"] = [
            str(c).strip() for c in patch["capabilities"] if str(c).strip()
        ]
    if "linked_camera_ids" in patch and isinstance(patch["linked_camera_ids"], list):
        updates["linked_camera_ids"] = [
            str(x).strip() for x in patch["linked_camera_ids"] if str(x).strip()
        ]
    if "default_priority" in patch and patch["default_priority"] is not None:
        updates["default_priority"] = normalize_priority(patch["default_priority"])
    if "metadata" in patch and isinstance(patch["metadata"], dict):
        updates["metadata"] = sanitize_metadata(patch["metadata"])

    secret_plain: Optional[str] = None
    if patch.get("rotate_secret"):
        secret_plain = generate_integration_secret()
        updates["api_key_hash"] = hash_integration_secret(secret_plain)

    await devices_collection.update_one({"_id": oid}, {"$set": updates})
    fresh = await devices_collection.find_one({"_id": oid})
    assert fresh is not None
    return device_to_public(fresh, include_secret_once=secret_plain)


async def delete_device(device_id: str) -> bool:
    try:
        oid = ObjectId(device_id)
    except (InvalidId, TypeError) as exc:
        raise CccDeviceError("Invalid device id") from exc
    result = await devices_collection.delete_one({"_id": oid})
    return result.deleted_count > 0


async def get_device(device_id: str) -> Optional[dict[str, Any]]:
    try:
        oid = ObjectId(device_id)
    except (InvalidId, TypeError):
        return None
    doc = await devices_collection.find_one({"_id": oid})
    return device_to_public(doc) if doc else None


async def get_device_doc_by_uid(device_uid: str) -> Optional[dict]:
    return await devices_collection.find_one({"device_uid": (device_uid or "").strip()})


async def get_device_doc(device_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(device_id)
    except (InvalidId, TypeError):
        doc = await get_device_doc_by_uid(device_id)
        return doc
    return await devices_collection.find_one({"_id": oid})


async def list_devices(
    *,
    type: Optional[str] = None,
    enabled: Optional[bool] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit_n = max(1, min(int(limit or 50), LIST_PAGE_MAX))
    offset_n = max(0, int(offset or 0))
    query: dict[str, Any] = {}
    if type:
        query["type"] = type.strip().lower()
    if enabled is not None:
        query["enabled"] = bool(enabled)
    if status:
        query["status"] = status.strip().lower()
    if location:
        query["location"] = {"$regex": location.strip(), "$options": "i"}
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"name": {"$regex": needle, "$options": "i"}},
            {"device_uid": {"$regex": needle, "$options": "i"}},
            {"location": {"$regex": needle, "$options": "i"}},
        ]
    total = int(await devices_collection.count_documents(query))
    cursor = (
        devices_collection.find(query)
        .sort([("type", 1), ("name", 1)])
        .skip(offset_n)
        .limit(limit_n)
    )
    items = [device_to_public(d) async for d in cursor]
    return {
        "items": items,
        "total": total,
        "limit": limit_n,
        "offset": offset_n,
        "hard_count_cap": False,
        "opens_streams_on_register": False,
        "rdso_18_6_22_3": True,
        "rdso_18_6_22_12": True,
    }


async def touch_device_heartbeat(
    doc: dict,
    *,
    status: str = "online",
    health: str = "ok",
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    oid = doc["_id"]
    updates: dict[str, Any] = {
        "last_seen": _utcnow(),
        "updated_at": _utcnow(),
        "status": status if status in DEVICE_STATUSES else "online",
        "health": str(health or "ok")[:32],
    }
    if isinstance(metadata, dict) and metadata:
        merged = dict(doc.get("metadata") or {})
        merged.update(sanitize_metadata(metadata))
        updates["metadata"] = merged
    await devices_collection.update_one({"_id": oid}, {"$set": updates})
    fresh = await devices_collection.find_one({"_id": oid})
    assert fresh is not None
    return device_to_public(fresh)


def check_ingest_rate(doc: dict) -> tuple[bool, dict[str, Any]]:
    """Simple per-device window counter (persisted on next successful ingest)."""
    now = _utcnow()
    started = doc.get("ingest_window_started")
    count = int(doc.get("ingest_window_count") or 0)
    if not isinstance(started, datetime):
        return True, {"ingest_window_started": now, "ingest_window_count": 1}
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (now - started).total_seconds()
    if elapsed > INGEST_RATE_WINDOW_SECONDS:
        return True, {"ingest_window_started": now, "ingest_window_count": 1}
    if count >= INGEST_RATE_MAX_PER_WINDOW:
        return False, {}
    return True, {"ingest_window_count": count + 1}


def constant_time_compare_token(provided: str, expected_hash: str) -> bool:
    """bcrypt verify already constant-time; keep helper for tests."""
    return verify_integration_secret(provided, expected_hash)


def digest_secret_for_log(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
