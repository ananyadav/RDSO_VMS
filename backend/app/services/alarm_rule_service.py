"""Alarm rule persistence and validation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import alarm_rules_collection, camera_collection
from app.services.alarm_constants import (
    COOLDOWN_MAX_SECONDS,
    COOLDOWN_MIN_SECONDS,
    RECORDING_DURATION_DEFAULT_SECONDS,
    RECORDING_DURATION_MAX_SECONDS,
    RECORDING_DURATION_MIN_SECONDS,
    RULE_ACTIONS,
    RULE_NAME_MAX_LEN,
    SEVERITIES,
    SOURCE_TYPES,
)
from app.services.camera_identity import get_camera_by_ref
from app.services.alarm_rule_evaluator import default_rule_runtime

logger = logging.getLogger(__name__)


class AlarmRuleValidationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _normalize_camera_id(raw: Any) -> str:
    cid = str(raw or "").strip()
    if not cid:
        raise AlarmRuleValidationError("camera_id is required")
    try:
        ObjectId(cid)
    except (InvalidId, TypeError) as exc:
        raise AlarmRuleValidationError("camera_id must be a valid MongoDB id") from exc
    return cid


async def _camera_exists(camera_id: str) -> dict:
    try:
        doc = await camera_collection.find_one({"_id": ObjectId(camera_id)})
    except (InvalidId, TypeError):
        doc = None
    if not doc:
        raise AlarmRuleValidationError("Camera not found")
    return doc


def _validate_recording_config(data: dict, *, actions: list[str]) -> dict[str, Any] | None:
    wants_recording = "start_recording" in actions
    raw = data.get("recording")
    if not wants_recording:
        if raw not in (None, {}):
            raise AlarmRuleValidationError("recording config is only allowed when start_recording is selected")
        return None

    if not isinstance(raw, dict):
        raise AlarmRuleValidationError("recording must be an object when start_recording is selected")
    try:
        duration = int(raw.get("duration_seconds", RECORDING_DURATION_DEFAULT_SECONDS))
    except (TypeError, ValueError) as exc:
        raise AlarmRuleValidationError("recording.duration_seconds must be an integer") from exc
    if duration < RECORDING_DURATION_MIN_SECONDS or duration > RECORDING_DURATION_MAX_SECONDS:
        raise AlarmRuleValidationError(
            f"recording.duration_seconds must be between "
            f"{RECORDING_DURATION_MIN_SECONDS} and {RECORDING_DURATION_MAX_SECONDS}"
        )
    return {"duration_seconds": duration}


def validate_rule_payload(data: dict, *, partial: bool = False, existing: dict | None = None) -> dict:
    """Validate and normalize create/update payload."""
    if not isinstance(data, dict):
        raise AlarmRuleValidationError("Invalid payload")

    out: dict[str, Any] = {}

    if "name" in data or not partial:
        name = str(data.get("name") or "").strip()
        if not name:
            raise AlarmRuleValidationError("name is required")
        if len(name) > RULE_NAME_MAX_LEN:
            raise AlarmRuleValidationError(f"name must be at most {RULE_NAME_MAX_LEN} characters")
        out["name"] = name

    if "enabled" in data or not partial:
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise AlarmRuleValidationError("enabled must be a boolean")
        out["enabled"] = enabled

    if "camera_id" in data or not partial:
        out["camera_id"] = _normalize_camera_id(data.get("camera_id"))

    if "trigger" in data or not partial:
        trigger = data.get("trigger")
        if not isinstance(trigger, dict):
            raise AlarmRuleValidationError("trigger must be an object")
        source_type = str(trigger.get("source_type") or "").strip().lower()
        if source_type not in SOURCE_TYPES:
            raise AlarmRuleValidationError(f"Unsupported source_type: {source_type or '(empty)'}")
        out["trigger"] = {"source_type": source_type}

    if "actions" in data or not partial:
        actions = data.get("actions")
        if not isinstance(actions, list) or not actions:
            raise AlarmRuleValidationError("actions must be a non-empty list")
        normalized: list[str] = []
        for action in actions:
            key = str(action or "").strip().lower()
            if key not in RULE_ACTIONS:
                raise AlarmRuleValidationError(f"Unsupported action: {action}")
            if key not in normalized:
                normalized.append(key)
        out["actions"] = normalized

    if "severity" in data or not partial:
        severity = str(data.get("severity") or "").strip().lower()
        if severity not in SEVERITIES:
            raise AlarmRuleValidationError(f"Unsupported severity: {severity or '(empty)'}")
        out["severity"] = severity

    if "cooldown_seconds" in data or not partial:
        raw = data.get("cooldown_seconds", 60)
        try:
            cooldown = int(raw)
        except (TypeError, ValueError) as exc:
            raise AlarmRuleValidationError("cooldown_seconds must be an integer") from exc
        if cooldown < COOLDOWN_MIN_SECONDS or cooldown > COOLDOWN_MAX_SECONDS:
            raise AlarmRuleValidationError(
                f"cooldown_seconds must be between {COOLDOWN_MIN_SECONDS} and {COOLDOWN_MAX_SECONDS}"
            )
        out["cooldown_seconds"] = cooldown

    merged_actions = out.get("actions")
    if merged_actions is None and existing:
        merged_actions = list(existing.get("actions") or [])
    elif merged_actions is None and not partial:
        merged_actions = []

    wants_recording = bool(merged_actions and "start_recording" in merged_actions)
    if wants_recording and "recording" not in data:
        if not partial or not (existing or {}).get("recording"):
            raise AlarmRuleValidationError(
                "recording.duration_seconds is required when start_recording is selected"
            )
    if "recording" in data or wants_recording:
        recording = _validate_recording_config(
            data if "recording" in data else {"recording": (existing or {}).get("recording")},
            actions=list(merged_actions or []),
        )
        if recording is not None:
            out["recording"] = recording
        elif not wants_recording and ("recording" in data or "actions" in out):
            out["recording"] = None

    return out


def rule_to_public(doc: dict) -> dict:
    runtime = doc.get("runtime") or {}
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "enabled": bool(doc.get("enabled")),
        "camera_id": doc.get("camera_id") or "",
        "trigger": doc.get("trigger") or {},
        "actions": list(doc.get("actions") or []),
        "severity": doc.get("severity") or "warning",
        "cooldown_seconds": int(doc.get("cooldown_seconds") or 0),
        "recording": doc.get("recording"),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "runtime": {
            "last_triggered_at": runtime.get("last_triggered_at"),
            "last_event_id": runtime.get("last_event_id"),
            "trigger_count": int(runtime.get("trigger_count") or 0),
        },
    }


async def ensure_alarm_rule_indexes() -> None:
    try:
        await alarm_rules_collection.create_index("enabled", name="idx_alarm_rule_enabled")
        await alarm_rules_collection.create_index("camera_id", name="idx_alarm_rule_camera_id")
        await alarm_rules_collection.create_index(
            "trigger.source_type",
            name="idx_alarm_rule_source_type",
        )
        await alarm_rules_collection.create_index("created_at", name="idx_alarm_rule_created_at")
    except Exception as exc:
        logger.warning("[alarm-rules] index creation skipped or partial: %s", exc)


async def list_alarm_rules(
    *,
    camera_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    source_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    query: dict[str, Any] = {}
    if camera_id:
        query["camera_id"] = _normalize_camera_id(camera_id)
    if enabled is not None:
        query["enabled"] = bool(enabled)
    if source_type:
        st = str(source_type).strip().lower()
        if st in SOURCE_TYPES:
            query["trigger.source_type"] = st

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total = await alarm_rules_collection.count_documents(query)
    items = []
    cursor = alarm_rules_collection.find(query).sort("created_at", -1).skip(offset).limit(limit)
    async for doc in cursor:
        items.append(rule_to_public(doc))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def get_alarm_rule(rule_id: str) -> Optional[dict]:
    rid = (rule_id or "").strip()
    try:
        doc = await alarm_rules_collection.find_one({"_id": ObjectId(rid)})
    except (InvalidId, TypeError):
        return None
    return rule_to_public(doc) if doc else None


async def create_alarm_rule(data: dict, *, created_by: str) -> dict:
    payload = validate_rule_payload(data, partial=False)
    await _camera_exists(payload["camera_id"])
    now = _utcnow()
    doc = {
        **payload,
        "runtime": default_rule_runtime(),
        "created_by": str(created_by),
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    result = await alarm_rules_collection.insert_one(doc)
    created = await alarm_rules_collection.find_one({"_id": result.inserted_id})
    return rule_to_public(created)


async def update_alarm_rule(rule_id: str, data: dict) -> Optional[dict]:
    rid = (rule_id or "").strip()
    try:
        oid = ObjectId(rid)
    except (InvalidId, TypeError):
        return None

    existing = await alarm_rules_collection.find_one({"_id": oid})
    if not existing:
        return None

    payload = validate_rule_payload(data, partial=True, existing=existing)
    if "camera_id" in payload:
        await _camera_exists(payload["camera_id"])
    if not payload:
        return rule_to_public(existing)

    payload["updated_at"] = _iso(_utcnow())
    unset_fields: dict[str, str] = {}
    if payload.get("recording") is None:
        unset_fields["recording"] = ""
        payload = {k: v for k, v in payload.items() if k != "recording"}
    update_doc: dict[str, Any] = {"$set": payload}
    if unset_fields:
        update_doc["$unset"] = unset_fields
    await alarm_rules_collection.update_one({"_id": oid}, update_doc)
    updated = await alarm_rules_collection.find_one({"_id": oid})
    return rule_to_public(updated)


async def delete_alarm_rule(rule_id: str) -> bool:
    rid = (rule_id or "").strip()
    try:
        oid = ObjectId(rid)
    except (InvalidId, TypeError):
        return False
    result = await alarm_rules_collection.delete_one({"_id": oid})
    return result.deleted_count > 0


async def get_alarm_rule_doc(rule_id: str) -> Optional[dict]:
    rid = (rule_id or "").strip()
    try:
        return await alarm_rules_collection.find_one({"_id": ObjectId(rid)})
    except (InvalidId, TypeError):
        return None
