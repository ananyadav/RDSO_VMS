"""Event occurrence persistence — internal create + query/ack APIs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection, events_collection
from app.services.alarm_constants import (
    EVENT_METADATA_MAX_JSON_BYTES,
    EVENT_METADATA_MAX_KEYS,
    EVENT_STATUSES,
    RECORDING_ACTION_STATUSES,
    SEVERITIES,
    SOURCE_TYPES,
)
from app.services.audit_service import redact_value
from app.services.camera_access import build_access_filter, is_admin, merge_query, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_uid import make_camera_uid

logger = logging.getLogger(__name__)


class EventValidationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(raw: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def sanitize_event_metadata(metadata: Optional[dict]) -> dict:
    if not metadata or not isinstance(metadata, dict):
        return {}
    out: dict[str, Any] = {}
    for idx, (key, value) in enumerate(metadata.items()):
        if idx >= EVENT_METADATA_MAX_KEYS:
            break
        k = str(key or "").strip()
        if not k:
            continue
        out[k] = redact_value(k, value)
    try:
        encoded = json.dumps(out, default=str)
    except (TypeError, ValueError):
        return {}
    if len(encoded.encode("utf-8")) > EVENT_METADATA_MAX_JSON_BYTES:
        return {"truncated": True}
    return out


def event_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "camera_id": doc.get("camera_id") or "",
        "camera_uid": doc.get("camera_uid") or "",
        "rule_id": doc.get("rule_id"),
        "source_type": doc.get("source_type") or "",
        "severity": doc.get("severity") or "info",
        "title": doc.get("title") or "",
        "message": doc.get("message") or "",
        "occurred_at": doc.get("occurred_at"),
        "status": doc.get("status") or "open",
        "acknowledged": bool(doc.get("acknowledged")),
        "acknowledged_by": doc.get("acknowledged_by"),
        "acknowledged_at": doc.get("acknowledged_at"),
        "actions_triggered": list(doc.get("actions_triggered") or []),
        "ui_notification": bool(doc.get("ui_notification")),
        "metadata": doc.get("metadata") or {},
        "recording_session_id": doc.get("recording_session_id"),
        "recording_status": doc.get("recording_status"),
    }


async def build_event_access_filter(user: Optional[dict]) -> dict[str, Any]:
    """Mongo filter fragment limiting events to cameras the user may access."""
    if not user or is_admin(user):
        return {}
    cam_filter = build_access_filter(user)
    if not cam_filter:
        return {}

    allowed_ids: set[str] = set()
    allowed_uids: set[str] = set()

    async def _add_group_cameras(groups: list[str]) -> None:
        if not groups:
            return
        async for cam in camera_collection.find(
            {"camera_group": {"$in": groups}},
            {"_id": 1, "camera_uid": 1},
        ):
            allowed_ids.add(str(cam["_id"]))
            uid = (cam.get("camera_uid") or "").strip()
            if uid:
                allowed_uids.add(uid)

    if "_id" in cam_filter and "$in" in cam_filter.get("_id", {}):
        allowed_ids.update(str(x) for x in cam_filter["_id"]["$in"])
    if "camera_uid" in cam_filter and "$in" in cam_filter.get("camera_uid", {}):
        allowed_uids.update(cam_filter["camera_uid"]["$in"])
    if "camera_group" in cam_filter and "$in" in cam_filter.get("camera_group", {}):
        await _add_group_cameras(cam_filter["camera_group"]["$in"])

    if "$or" in cam_filter:
        for part in cam_filter["$or"]:
            if "_id" in part and "$in" in part.get("_id", {}):
                allowed_ids.update(str(x) for x in part["_id"]["$in"])
            if "camera_uid" in part and "$in" in part.get("camera_uid", {}):
                allowed_uids.update(part["camera_uid"]["$in"])
            if "camera_group" in part and "$in" in part.get("camera_group", {}):
                await _add_group_cameras(part["camera_group"]["$in"])

    clauses: list[dict[str, Any]] = []
    if allowed_ids:
        clauses.append({"camera_id": {"$in": list(allowed_ids)}})
    if allowed_uids:
        clauses.append({"camera_uid": {"$in": list(allowed_uids)}})
    if not clauses:
        return {"_id": {"$exists": False}}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


async def ensure_event_indexes() -> None:
    try:
        await events_collection.create_index("occurred_at", name="idx_event_occurred_at")
        await events_collection.create_index("camera_id", name="idx_event_camera_id")
        await events_collection.create_index("source_type", name="idx_event_source_type")
        await events_collection.create_index("status", name="idx_event_status")
        await events_collection.create_index("acknowledged", name="idx_event_acknowledged")
        await events_collection.create_index("rule_id", name="idx_event_rule_id")
        await events_collection.create_index(
            [("occurred_at", -1), ("camera_id", 1)],
            name="idx_event_occurred_camera",
        )
    except Exception as exc:
        logger.warning("[events] index creation skipped or partial: %s", exc)


async def create_event(
    *,
    camera_id: str,
    source_type: str,
    severity: str,
    title: str,
    message: str,
    camera_uid: Optional[str] = None,
    rule_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    occurred_at: Optional[datetime] = None,
    actions_triggered: Optional[list[str]] = None,
    ui_notification: bool = False,
) -> dict:
    """Internal API for rule evaluator and future adapters — not exposed via HTTP."""
    st = str(source_type or "").strip().lower()
    if st not in SOURCE_TYPES:
        raise EventValidationError(f"Unsupported source_type: {source_type}")
    sev = str(severity or "").strip().lower()
    if sev not in SEVERITIES:
        raise EventValidationError(f"Unsupported severity: {severity}")

    cid = str(camera_id or "").strip()
    try:
        ObjectId(cid)
    except (InvalidId, TypeError) as exc:
        raise EventValidationError("camera_id must be a valid MongoDB id") from exc

    cam = await get_camera_by_ref(cid)
    if not cam:
        raise EventValidationError("Camera not found")

    uid = (camera_uid or cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "") or cid).strip()
    when = occurred_at or _utcnow()
    rid = None
    if rule_id:
        try:
            rid = str(ObjectId(str(rule_id)))
        except (InvalidId, TypeError) as exc:
            raise EventValidationError("rule_id must be a valid MongoDB id") from exc

    doc = {
        "camera_id": cid,
        "camera_uid": uid,
        "rule_id": rid,
        "source_type": st,
        "severity": sev,
        "title": str(title or "").strip()[:200],
        "message": str(message or "").strip()[:2000],
        "occurred_at": _iso(when),
        "status": "open",
        "acknowledged": False,
        "acknowledged_by": None,
        "acknowledged_at": None,
        "actions_triggered": [str(a) for a in (actions_triggered or [])],
        "ui_notification": bool(ui_notification),
        "metadata": sanitize_event_metadata(metadata),
    }
    result = await events_collection.insert_one(doc)
    inserted = await events_collection.find_one({"_id": result.inserted_id})
    return event_to_public(inserted)


async def update_event_recording_result(
    event_id: str,
    *,
    recording_status: str,
    recording_session_id: Optional[str] = None,
) -> Optional[dict]:
    """Attach alarm recording action outcome to a persisted event."""
    eid = (event_id or "").strip()
    try:
        oid = ObjectId(eid)
    except (InvalidId, TypeError):
        return None

    status = str(recording_status or "").strip().lower()
    if status not in RECORDING_ACTION_STATUSES:
        status = "failed"

    updates: dict[str, Any] = {"recording_status": status}
    if recording_session_id:
        updates["recording_session_id"] = str(recording_session_id)

    await events_collection.update_one({"_id": oid}, {"$set": updates})
    doc = await events_collection.find_one({"_id": oid})
    return event_to_public(doc) if doc else None


async def list_events(
    user: dict,
    *,
    camera_id: Optional[str] = None,
    source_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    ui_notification: Optional[bool] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    query_parts: list[dict[str, Any]] = []
    access = await build_event_access_filter(user)
    if access:
        query_parts.append(access)

    if camera_id:
        cid = str(camera_id).strip()
        query_parts.append({"camera_id": cid})
    if source_type:
        st = str(source_type).strip().lower()
        if st in SOURCE_TYPES:
            query_parts.append({"source_type": st})
    if severity:
        sev = str(severity).strip().lower()
        if sev in SEVERITIES:
            query_parts.append({"severity": sev})
    if status:
        st_status = str(status).strip().lower()
        if st_status in EVENT_STATUSES:
            query_parts.append({"status": st_status})
    if acknowledged is not None:
        query_parts.append({"acknowledged": bool(acknowledged)})
    if ui_notification is not None:
        query_parts.append({"ui_notification": bool(ui_notification)})

    time_clause: dict[str, Any] = {}
    if from_ts:
        dt = _parse_iso(from_ts)
        if dt:
            time_clause["$gte"] = _iso(dt)
    if to_ts:
        dt = _parse_iso(to_ts)
        if dt:
            time_clause["$lte"] = _iso(dt)
    if time_clause:
        query_parts.append({"occurred_at": time_clause})

    query = merge_query(*query_parts)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total = await events_collection.count_documents(query)
    items = []
    cursor = events_collection.find(query).sort("occurred_at", -1).skip(offset).limit(limit)
    async for doc in cursor:
        items.append(event_to_public(doc))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_event(event_id: str, user: dict) -> Optional[dict]:
    eid = (event_id or "").strip()
    try:
        doc = await events_collection.find_one({"_id": ObjectId(eid)})
    except (InvalidId, TypeError):
        return None
    if not doc:
        return None
    cam = await get_camera_by_ref(doc.get("camera_id") or "")
    if not user_can_access_camera(user, doc.get("camera_id") or "", cam):
        return None
    return event_to_public(doc)


async def acknowledge_event(event_id: str, user: dict) -> Optional[dict]:
    eid = (event_id or "").strip()
    try:
        oid = ObjectId(eid)
    except (InvalidId, TypeError):
        return None

    doc = await events_collection.find_one({"_id": oid})
    if not doc:
        return None

    cam = await get_camera_by_ref(doc.get("camera_id") or "")
    if not user_can_access_camera(user, doc.get("camera_id") or "", cam):
        return None

    now = _utcnow()
    actor_id = str(user.get("_id") or user.get("id") or "")
    await events_collection.update_one(
        {"_id": oid},
        {
            "$set": {
                "acknowledged": True,
                "acknowledged_by": actor_id,
                "acknowledged_at": _iso(now),
                "status": "acknowledged",
            }
        },
    )
    updated = await events_collection.find_one({"_id": oid})
    return event_to_public(updated)
