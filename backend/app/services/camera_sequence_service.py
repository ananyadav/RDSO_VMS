"""Camera sequence persistence, validation, and ACL-aware public views."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection, camera_sequences_collection
from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_sequence_constants import (
    DESCRIPTION_MAX_LEN,
    DWELL_DEFAULT_SECONDS,
    DWELL_MAX_SECONDS,
    DWELL_MIN_SECONDS,
    FORBIDDEN_PAYLOAD_KEYS,
    MIN_CAMERAS,
    NAME_MAX_LEN,
)

logger = logging.getLogger(__name__)


class CameraSequenceValidationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _reject_forbidden_keys(data: dict) -> None:
    for key in data:
        normalized = str(key or "").strip().lower()
        if normalized in FORBIDDEN_PAYLOAD_KEYS:
            raise CameraSequenceValidationError(f"Field not allowed: {key}")
        if "password" in normalized or normalized.endswith("_secret"):
            raise CameraSequenceValidationError(f"Field not allowed: {key}")


def _normalize_object_id(raw: Any, *, field: str) -> str:
    cid = str(raw or "").strip()
    if not cid:
        raise CameraSequenceValidationError(f"{field} must be a valid MongoDB id")
    try:
        ObjectId(cid)
    except (InvalidId, TypeError) as exc:
        raise CameraSequenceValidationError(f"{field} must be a valid MongoDB id") from exc
    return cid


def _normalize_camera_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise CameraSequenceValidationError("camera_ids must be a non-empty list")
    if len(raw) < MIN_CAMERAS:
        raise CameraSequenceValidationError(f"camera_ids must contain at least {MIN_CAMERAS} cameras")
    out: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw):
        cid = _normalize_object_id(item, field=f"camera_ids[{idx}]")
        if cid in seen:
            raise CameraSequenceValidationError("camera_ids must not contain duplicates")
        seen.add(cid)
        out.append(cid)
    return out


async def _cameras_exist_ordered(camera_ids: list[str]) -> None:
    oids = [ObjectId(cid) for cid in camera_ids]
    found: set[str] = set()
    async for cam in camera_collection.find({"_id": {"$in": oids}}, {"_id": 1}):
        found.add(str(cam["_id"]))
    missing = [cid for cid in camera_ids if cid not in found]
    if missing:
        raise CameraSequenceValidationError("One or more cameras were not found")


async def _load_cameras_by_id(camera_ids: list[str]) -> dict[str, dict]:
    if not camera_ids:
        return {}
    oids = [ObjectId(cid) for cid in camera_ids]
    out: dict[str, dict] = {}
    async for cam in camera_collection.find({"_id": {"$in": oids}}):
        out[str(cam["_id"])] = cam
    return out


def filter_authorized_camera_ids(
    user: Optional[dict],
    ordered_ids: list[str],
    cameras_by_id: dict[str, dict],
) -> list[str]:
    """Preserve stored order; return only cameras the user may access."""
    if is_admin(user):
        return list(ordered_ids)
    authorized: list[str] = []
    for cid in ordered_ids:
        cam = cameras_by_id.get(cid)
        if cam and user_can_access_camera(user, cid, cam):
            authorized.append(cid)
    return authorized


def sequence_to_public(
    doc: dict,
    *,
    user: Optional[dict] = None,
    cameras_by_id: Optional[dict[str, dict]] = None,
) -> dict:
    stored_ids = [str(cid) for cid in (doc.get("camera_ids") or [])]
    if cameras_by_id is None:
        public_ids = stored_ids if is_admin(user) else []
    else:
        public_ids = filter_authorized_camera_ids(user, stored_ids, cameras_by_id)

    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "enabled": bool(doc.get("enabled")),
        "camera_ids": public_ids,
        "dwell_seconds": int(doc.get("dwell_seconds") or DWELL_DEFAULT_SECONDS),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def validate_sequence_payload(data: dict, *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CameraSequenceValidationError("Invalid payload")
    _reject_forbidden_keys(data)

    out: dict[str, Any] = {}

    if "name" in data or not partial:
        name = str(data.get("name") or "").strip()
        if not name:
            raise CameraSequenceValidationError("name is required")
        if len(name) > NAME_MAX_LEN:
            raise CameraSequenceValidationError(f"name must be at most {NAME_MAX_LEN} characters")
        out["name"] = name

    if "description" in data or not partial:
        description = str(data.get("description") or "").strip()
        if len(description) > DESCRIPTION_MAX_LEN:
            raise CameraSequenceValidationError(
                f"description must be at most {DESCRIPTION_MAX_LEN} characters"
            )
        out["description"] = description

    if "enabled" in data or not partial:
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise CameraSequenceValidationError("enabled must be a boolean")
        out["enabled"] = enabled

    if "camera_ids" in data or not partial:
        out["camera_ids"] = _normalize_camera_ids(data.get("camera_ids"))

    if "dwell_seconds" in data or not partial:
        raw = data.get("dwell_seconds", DWELL_DEFAULT_SECONDS)
        try:
            dwell = int(raw)
        except (TypeError, ValueError) as exc:
            raise CameraSequenceValidationError("dwell_seconds must be an integer") from exc
        if dwell < DWELL_MIN_SECONDS or dwell > DWELL_MAX_SECONDS:
            raise CameraSequenceValidationError(
                f"dwell_seconds must be between {DWELL_MIN_SECONDS} and {DWELL_MAX_SECONDS}"
            )
        out["dwell_seconds"] = dwell

    return out


async def ensure_camera_sequence_indexes() -> None:
    try:
        await camera_sequences_collection.create_index("name", name="idx_camera_sequence_name")
        await camera_sequences_collection.create_index("enabled", name="idx_camera_sequence_enabled")
        await camera_sequences_collection.create_index("created_at", name="idx_camera_sequence_created_at")
        await camera_sequences_collection.create_index("created_by", name="idx_camera_sequence_created_by")
    except Exception as exc:
        logger.warning("[camera-sequences] index creation skipped or partial: %s", exc)


async def get_sequence_doc(sequence_id: str) -> Optional[dict]:
    sid = (sequence_id or "").strip()
    try:
        return await camera_sequences_collection.find_one({"_id": ObjectId(sid)})
    except (InvalidId, TypeError):
        return None


async def list_camera_sequences(
    user: Optional[dict],
    *,
    enabled: Optional[bool] = None,
    include_disabled_for_admin: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    query: dict[str, Any] = {}
    admin = is_admin(user)
    if not admin:
        query["enabled"] = True
    elif enabled is not None:
        query["enabled"] = bool(enabled)
    elif not include_disabled_for_admin and enabled is None:
        query["enabled"] = True

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    docs: list[dict] = []
    cursor = camera_sequences_collection.find(query).sort("created_at", -1)
    async for doc in cursor:
        docs.append(doc)

    all_camera_ids: set[str] = set()
    for doc in docs:
        for cid in doc.get("camera_ids") or []:
            all_camera_ids.add(str(cid))

    cameras_by_id = await _load_cameras_by_id(list(all_camera_ids))

    items: list[dict] = []
    for doc in docs:
        public = sequence_to_public(doc, user=user, cameras_by_id=cameras_by_id)
        if admin or public["camera_ids"]:
            items.append(public)

    total = len(items)
    page = items[offset : offset + limit]
    return {"items": page, "total": total, "limit": limit, "offset": offset}


async def get_camera_sequence(sequence_id: str, user: Optional[dict]) -> Optional[dict]:
    doc = await get_sequence_doc(sequence_id)
    if not doc:
        return None

    admin = is_admin(user)
    if not admin and not doc.get("enabled"):
        return None

    stored_ids = [str(cid) for cid in (doc.get("camera_ids") or [])]
    cameras_by_id = await _load_cameras_by_id(stored_ids)
    public = sequence_to_public(doc, user=user, cameras_by_id=cameras_by_id)

    if not admin and not public["camera_ids"]:
        return None

    return public


async def create_camera_sequence(data: dict, *, created_by: str) -> dict:
    payload = validate_sequence_payload(data, partial=False)
    await _cameras_exist_ordered(payload["camera_ids"])
    now = _utcnow()
    doc = {
        **payload,
        "created_by": str(created_by),
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    result = await camera_sequences_collection.insert_one(doc)
    created = await camera_sequences_collection.find_one({"_id": result.inserted_id})
    return sequence_to_public(created, user={"role": "Admin"})


async def update_camera_sequence(sequence_id: str, data: dict) -> Optional[dict]:
    sid = (sequence_id or "").strip()
    try:
        oid = ObjectId(sid)
    except (InvalidId, TypeError):
        return None

    existing = await camera_sequences_collection.find_one({"_id": oid})
    if not existing:
        return None

    payload = validate_sequence_payload(data, partial=True)
    if "camera_ids" in payload:
        await _cameras_exist_ordered(payload["camera_ids"])
    if not payload:
        return sequence_to_public(existing, user={"role": "Admin"})

    payload["updated_at"] = _iso(_utcnow())
    await camera_sequences_collection.update_one({"_id": oid}, {"$set": payload})
    updated = await camera_sequences_collection.find_one({"_id": oid})
    return sequence_to_public(updated, user={"role": "Admin"})


async def delete_camera_sequence(sequence_id: str) -> bool:
    sid = (sequence_id or "").strip()
    try:
        oid = ObjectId(sid)
    except (InvalidId, TypeError):
        return False
    result = await camera_sequences_collection.delete_one({"_id": oid})
    return result.deleted_count > 0
