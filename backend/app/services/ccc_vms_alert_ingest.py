"""Normalize external VMS alerts into existing create_event pipeline (18.6.17.3 / 18.6.22.8)."""

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
from app.services.ccc_device_service import INGEST_BODY_MAX_BYTES
from app.services.ccc_integration_idempotency import (
    extract_idempotency_key,
    lookup_idempotent_result,
    store_idempotent_result,
)
from app.services.ccc_open_integration import extract_location_metadata
from app.services.event_service import EventValidationError, create_event


class VmsAlertIngestError(ValueError):
    pass


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
        raise VmsAlertIngestError("Invalid occurred_at") from exc


def _normalize_source_type(raw: Any) -> str:
    st = str(raw or "external_sensor").strip().lower()
    st = SOURCE_TYPE_ALIASES.get(st, st)
    if st not in SOURCE_TYPES:
        raise VmsAlertIngestError(f"Unsupported source_type: {raw}")
    return st


def validate_vms_alert_payload_size(body: dict) -> None:
    try:
        raw = json.dumps(body, default=str)
    except (TypeError, ValueError) as exc:
        raise VmsAlertIngestError("Malformed payload") from exc
    if len(raw.encode("utf-8")) > INGEST_BODY_MAX_BYTES:
        raise VmsAlertIngestError(f"Payload exceeds {INGEST_BODY_MAX_BYTES} bytes")


async def ingest_vms_alert(
    *,
    source_id: str,
    body: dict[str, Any],
    linked_local_camera_id: str = "",
    idempotency_header: str = "",
) -> dict[str, Any]:
    """
    Map purchaser/external VMS alert JSON → create_event.
    Does not start a second alarm engine.
    """
    if not isinstance(body, dict):
        raise VmsAlertIngestError("JSON object required")
    validate_vms_alert_payload_size(body)

    scope = f"vms:{source_id}"
    idem_key = extract_idempotency_key(body, header_key=idempotency_header)
    if idem_key:
        prior = await lookup_idempotent_result(scope, idem_key)
        if prior:
            return {
                **prior,
                "ok": True,
                "accepted": True,
                "duplicate": True,
                "via_existing_pipeline": True,
                "second_alarm_engine": False,
                "source_id": source_id,
            }

    title = str(body.get("title") or body.get("name") or "External VMS alert")[:200]
    message = str(body.get("message") or body.get("description") or "")[:2000]
    severity = str(body.get("severity") or "warning").strip().lower()
    if severity not in ("info", "warning", "critical"):
        severity = "warning"
    source_type = _normalize_source_type(body.get("source_type") or body.get("type"))
    priority = body.get("priority")
    occurred_at = _parse_ts(body.get("occurred_at") or body.get("timestamp"))
    camera_id = str(
        body.get("local_camera_id")
        or linked_local_camera_id
        or body.get("camera_id")
        or ""
    ).strip()
    # External-only alerts without local camera mapping use external_sensor.
    if source_type != "external_sensor" and not camera_id:
        raise VmsAlertIngestError(
            "camera_id or local_camera_id required unless source_type=external_sensor"
        )
    meta_in = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if len(meta_in) > EVENT_METADATA_MAX_KEYS:
        raise VmsAlertIngestError(f"metadata exceeds {EVENT_METADATA_MAX_KEYS} keys")
    md = sanitize_metadata(meta_in)
    md["vms_source_id"] = source_id
    md["external_alert"] = True
    if body.get("external_camera_id"):
        md["external_camera_id"] = str(body.get("external_camera_id"))
    loc_fields = extract_location_metadata(body)
    md.update(loc_fields)
    if idem_key:
        md["external_event_id"] = idem_key
    meta_json = json.dumps(md, default=str)
    if len(meta_json.encode("utf-8")) > EVENT_METADATA_MAX_JSON_BYTES:
        raise VmsAlertIngestError("metadata too large")
    try:
        event = await create_event(
            camera_id=camera_id,
            source_type=source_type,
            severity=severity,
            title=title,
            message=message,
            metadata=md,
            occurred_at=occurred_at,
            priority=int(priority) if priority is not None else None,
            ui_notification=True,
            actions_triggered=[f"vms_integration:{source_id}"],
        )
    except EventValidationError as exc:
        raise VmsAlertIngestError(str(exc)) from exc
    result = {
        "ok": True,
        "accepted": True,
        "event_id": event.get("id"),
        "source_id": source_id,
        "via_existing_pipeline": True,
        "second_alarm_engine": False,
        "duplicate": False,
        "event_created": True,
    }
    if idem_key:
        await store_idempotent_result(scope, idem_key, result)
    return result
