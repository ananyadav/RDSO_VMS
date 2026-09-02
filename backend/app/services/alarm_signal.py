"""Normalized internal alarm signal — adapter input to the rule evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.services.alarm_constants import SOURCE_TYPES
from app.services.event_service import sanitize_event_metadata


class AlarmSignalValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedAlarmSignal:
    camera_id: str
    camera_uid: str
    source_type: str
    occurred_at: datetime
    title: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_occurred_at(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
    elif raw is None or raw == "":
        dt = _utcnow()
    else:
        text = str(raw).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AlarmSignalValidationError("occurred_at must be a valid ISO-8601 timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_alarm_signal(raw: dict) -> NormalizedAlarmSignal:
    """Validate and normalize an adapter/test alarm signal."""
    if not isinstance(raw, dict):
        raise AlarmSignalValidationError("signal must be an object")

    camera_id = str(raw.get("camera_id") or "").strip()
    if not camera_id:
        raise AlarmSignalValidationError("camera_id is required")
    try:
        ObjectId(camera_id)
    except (InvalidId, TypeError) as exc:
        raise AlarmSignalValidationError("camera_id must be a valid MongoDB id") from exc

    source_type = str(raw.get("source_type") or "").strip().lower()
    if source_type not in SOURCE_TYPES:
        raise AlarmSignalValidationError(f"Unsupported source_type: {source_type or '(empty)'}")

    camera_uid = str(raw.get("camera_uid") or "").strip()
    title = str(raw.get("title") or "").strip()[:200]
    message = str(raw.get("message") or "").strip()[:2000]
    if not title:
        raise AlarmSignalValidationError("title is required")
    if not message:
        raise AlarmSignalValidationError("message is required")

    return NormalizedAlarmSignal(
        camera_id=camera_id,
        camera_uid=camera_uid,
        source_type=source_type,
        occurred_at=_parse_occurred_at(raw.get("occurred_at")),
        title=title,
        message=message,
        metadata=sanitize_event_metadata(raw.get("metadata")),
    )
