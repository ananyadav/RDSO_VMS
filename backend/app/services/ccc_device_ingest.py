"""CCC external sensor ingest → existing event pipeline (no second alarm engine)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.alarm_constants import (
    EVENT_METADATA_MAX_JSON_BYTES,
    EVENT_METADATA_MAX_KEYS,
    SOURCE_TYPE_ALIASES,
    SOURCE_TYPES,
)
from app.services.audit_service import sanitize_metadata
from app.services.ccc_device_service import (
    INGEST_BODY_MAX_BYTES,
    check_ingest_rate,
    devices_collection,
    touch_device_heartbeat,
    verify_integration_secret,
)
from app.services.ccc_integration_idempotency import (
    extract_idempotency_key,
    lookup_idempotent_result,
    store_idempotent_result,
)
from app.services.ccc_open_integration import extract_location_metadata
from app.services.event_service import EventValidationError, create_event
from app.services.priority_levels import resolve_alarm_priority


class CccIngestError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise CccIngestError("Invalid timestamp") from exc


def _normalize_source_type(raw: Any, device_type: str) -> str:
    st = str(raw or "").strip().lower()
    if not st:
        if device_type == "camera_digital_input":
            return "digital_input"
        return "external_sensor"
    st = SOURCE_TYPE_ALIASES.get(st, st)
    if st not in SOURCE_TYPES:
        raise CccIngestError(f"Unsupported source_type: {raw}")
    return st


def validate_ingest_payload_size(body: dict) -> None:
    try:
        raw = json.dumps(body, default=str)
    except (TypeError, ValueError) as exc:
        raise CccIngestError("Malformed payload") from exc
    if len(raw.encode("utf-8")) > INGEST_BODY_MAX_BYTES:
        raise CccIngestError(f"Payload exceeds {INGEST_BODY_MAX_BYTES} bytes")


async def authenticate_device_ingest(doc: dict, provided_secret: str) -> None:
    if not doc.get("enabled", True):
        raise CccIngestError("Device disabled")
    hashed = doc.get("api_key_hash") or ""
    if not hashed:
        raise CccIngestError("Device has no integration secret configured")
    if not provided_secret or not verify_integration_secret(provided_secret, hashed):
        raise CccIngestError("Invalid integration secret")


async def ingest_device_alert(
    doc: dict,
    *,
    body: dict,
    idempotency_header: str = "",
) -> dict[str, Any]:
    """Normalize external/device alert into create_event (existing pipeline)."""
    if not isinstance(body, dict):
        raise CccIngestError("JSON object required")
    validate_ingest_payload_size(body)

    scope = f"device:{doc['_id']}"
    idem_key = extract_idempotency_key(body, header_key=idempotency_header)
    if idem_key:
        prior = await lookup_idempotent_result(scope, idem_key)
        if prior:
            return {
                **prior,
                "ok": True,
                "accepted": True,
                "duplicate": True,
                "event_created": False,
            }

    ok_rate, rate_updates = check_ingest_rate(doc)
    if not ok_rate:
        raise CccIngestError("Rate limit exceeded for device")

    kind = str(body.get("kind") or body.get("message_type") or "alert").strip().lower()
    status = str(body.get("status") or body.get("state") or "online").strip().lower()
    health = str(body.get("health") or "ok").strip().lower()[:32]

    # Heartbeat / status-only path — no event.
    if kind in ("heartbeat", "status", "health"):
        device = await touch_device_heartbeat(
            doc, status=status or "online", health=health, metadata=body.get("metadata")
        )
        if rate_updates:
            await devices_collection.update_one({"_id": doc["_id"]}, {"$set": rate_updates})
        return {
            "ok": True,
            "accepted": True,
            "event_created": False,
            "device": device,
            "pipeline": "heartbeat_only",
        }

    title = str(body.get("title") or body.get("message") or "").strip()
    if not title:
        raise CccIngestError("title or message required for alert")

    severity = str(body.get("severity") or "warning").strip().lower()
    if severity not in ("info", "warning", "critical"):
        raise CccIngestError("severity must be info|warning|critical")

    priority = body.get("priority")
    if priority is None or priority == "":
        priority = doc.get("default_priority")
    try:
        pri = resolve_alarm_priority(priority=priority, severity=severity)
    except Exception as exc:
        raise CccIngestError(str(exc)) from exc

    loc_fields = extract_location_metadata(body)
    if not loc_fields.get("location") and doc.get("location"):
        loc_fields["location"] = str(doc.get("location")).strip()[:500]
    location = str(loc_fields.get("location") or "").strip()
    occurred_at = _parse_ts(body.get("timestamp") or body.get("occurred_at"))

    source_type = _normalize_source_type(
        body.get("source_type") or body.get("event_type"), doc.get("type") or ""
    )

    meta_in = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if len(meta_in) > EVENT_METADATA_MAX_KEYS:
        raise CccIngestError(f"metadata exceeds {EVENT_METADATA_MAX_KEYS} keys")
    meta = sanitize_metadata(meta_in)
    meta["ccc_device_id"] = str(doc["_id"])
    meta["ccc_device_uid"] = doc.get("device_uid")
    meta["ccc_device_type"] = doc.get("type")
    meta.update(loc_fields)
    if idem_key:
        meta["external_event_id"] = idem_key
    meta_json = json.dumps(meta, default=str)
    if len(meta_json.encode("utf-8")) > EVENT_METADATA_MAX_JSON_BYTES:
        raise CccIngestError("metadata too large")

    cams = list(doc.get("linked_camera_ids") or [])
    camera_id = str(body.get("camera_id") or (cams[0] if cams else "")).strip()
    if camera_id and cams and camera_id not in cams:
        raise CccIngestError("camera_id not linked to this device")

    if camera_id:
        event_source = source_type if source_type != "external_sensor" else "digital_input"
        if event_source not in SOURCE_TYPES:
            event_source = "digital_input"
        try:
            event = await create_event(
                camera_id=camera_id,
                source_type=event_source,
                severity=severity,
                title=title[:200],
                message=str(body.get("message") or title)[:2000],
                priority=pri,
                occurred_at=occurred_at,
                ui_notification=True,
                actions_triggered=["ccc_device_ingest"],
                metadata=meta,
            )
        except EventValidationError as exc:
            raise CccIngestError(str(exc)) from exc
    else:
        if (doc.get("type") or "") in ("camera", "camera_digital_input"):
            raise CccIngestError("camera-linked device requires linked_camera_ids")
        try:
            event = await create_event(
                camera_id="",
                camera_uid=str(doc.get("device_uid") or doc["_id"]),
                source_type="external_sensor",
                severity=severity,
                title=title[:200],
                message=str(body.get("message") or title)[:2000],
                priority=pri,
                occurred_at=occurred_at,
                ui_notification=True,
                actions_triggered=["ccc_device_ingest"],
                metadata=meta,
            )
        except EventValidationError as exc:
            raise CccIngestError(str(exc)) from exc

    now = _utcnow()
    updates = {
        "last_seen": now,
        "updated_at": now,
        "status": "online",
        "health": health or "ok",
        "last_event_id": event.get("id"),
        "active_alert_count": int(doc.get("active_alert_count") or 0) + 1,
        **rate_updates,
    }
    await devices_collection.update_one({"_id": doc["_id"]}, {"$set": updates})

    result = {
        "ok": True,
        "accepted": True,
        "event_created": True,
        "event": event,
        "event_id": event.get("id"),
        "pipeline": "existing_event_service",
        "second_alarm_engine": False,
        "device_id": str(doc["_id"]),
        "priority": pri,
        "location": location,
        "duplicate": False,
    }
    if idem_key:
        await store_idempotent_result(scope, idem_key, result)
    return result
