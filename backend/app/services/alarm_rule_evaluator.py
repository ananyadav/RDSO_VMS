"""Internal alarm rule evaluator — matches signals to rules and executes actions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument

from app.core.database import alarm_rules_collection
from app.services.alarm_recording_service import start_alarm_triggered_recording
from app.services.alarm_constants import RULE_ACTIONS
from app.services.alarm_signal import (
    AlarmSignalValidationError,
    NormalizedAlarmSignal,
    normalize_alarm_signal,
)
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_uid import make_camera_uid
from app.services.event_service import EventValidationError, create_event, update_event_recording_result

logger = logging.getLogger(__name__)

RULE_STATUS_TRIGGERED = "triggered"
RULE_STATUS_SUPPRESSED = "suppressed_by_cooldown"
RULE_STATUS_FAILED = "failed"


def default_rule_runtime() -> dict[str, Any]:
    return {
        "last_triggered_at": None,
        "last_event_id": None,
        "trigger_count": 0,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def find_matching_enabled_rules(signal: NormalizedAlarmSignal) -> list[dict]:
    cursor = alarm_rules_collection.find(
        {
            "enabled": True,
            "camera_id": signal.camera_id,
            "trigger.source_type": signal.source_type,
        }
    )
    return [doc async for doc in cursor]


async def try_claim_rule_execution(rule: dict, *, now: datetime) -> bool:
    """Atomically claim a rule execution if cooldown allows. MongoDB-backed."""
    rule_id = rule["_id"]
    cooldown_seconds = int(rule.get("cooldown_seconds") or 0)
    now_iso = _iso(now)

    base_filter: dict[str, Any] = {"_id": rule_id, "enabled": True}
    if cooldown_seconds > 0:
        cutoff_iso = _iso(now - timedelta(seconds=cooldown_seconds))
        base_filter["$or"] = [
            {"runtime.last_triggered_at": {"$exists": False}},
            {"runtime.last_triggered_at": None},
            {"runtime.last_triggered_at": {"$lte": cutoff_iso}},
        ]

    claimed = await alarm_rules_collection.find_one_and_update(
        base_filter,
        {
            "$set": {"runtime.last_triggered_at": now_iso},
            "$inc": {"runtime.trigger_count": 1},
        },
        return_document=ReturnDocument.BEFORE,
    )
    return claimed is not None


async def _resolve_camera_uid(signal: NormalizedAlarmSignal, camera_doc: dict) -> str:
    uid = signal.camera_uid or (camera_doc.get("camera_uid") or "")
    uid = str(uid).strip()
    if uid:
        return uid
    ip = (camera_doc.get("ip_address") or "").strip()
    return make_camera_uid(ip) or signal.camera_id


def _actions_for_rule(rule: dict) -> list[str]:
    actions: list[str] = []
    for action in rule.get("actions") or []:
        key = str(action or "").strip().lower()
        if key in RULE_ACTIONS and key not in actions:
            actions.append(key)
    return actions


async def _execute_rule_actions(
    rule: dict,
    signal: NormalizedAlarmSignal,
    *,
    camera_uid: str,
) -> dict[str, Any]:
    """Persist one system-of-record event and mark requested actions."""
    rule_id = str(rule["_id"])
    configured_actions = _actions_for_rule(rule)
    ui_notification = "ui_notification" in configured_actions

    event = await create_event(
        camera_id=signal.camera_id,
        camera_uid=camera_uid,
        source_type=signal.source_type,
        severity=str(rule.get("severity") or "warning"),
        title=signal.title,
        message=signal.message,
        rule_id=rule_id,
        metadata=signal.metadata,
        occurred_at=signal.occurred_at,
        actions_triggered=configured_actions,
        ui_notification=ui_notification,
    )

    await alarm_rules_collection.update_one(
        {"_id": rule["_id"]},
        {"$set": {"runtime.last_event_id": event["id"]}},
    )

    recording_status = None
    recording_session_id = None
    if "start_recording" in configured_actions:
        recording_cfg = rule.get("recording") or {}
        duration_seconds = int(recording_cfg.get("duration_seconds") or 60)
        try:
            rec_result = await start_alarm_triggered_recording(
                signal.camera_id,
                event_id=event["id"],
                rule_id=rule_id,
                source_type=signal.source_type,
                duration_seconds=duration_seconds,
            )
            recording_status = rec_result.get("recording_status")
            recording_session_id = rec_result.get("recording_session_id")
        except Exception as exc:
            logger.error(
                "[alarm-evaluator] start_recording failed rule=%s event=%s: %s",
                rule_id,
                event["id"],
                exc,
                exc_info=True,
            )
            recording_status = "failed"
        try:
            await update_event_recording_result(
                event["id"],
                recording_status=str(recording_status or "failed"),
                recording_session_id=recording_session_id,
            )
        except Exception as exc:
            logger.warning(
                "[alarm-evaluator] failed to persist recording result event=%s: %s",
                event["id"],
                exc,
            )

    return {
        "rule_id": rule_id,
        "status": RULE_STATUS_TRIGGERED,
        "event_id": event["id"],
        "actions_triggered": configured_actions,
        "ui_notification": ui_notification,
        "recording_status": recording_status,
        "recording_session_id": recording_session_id,
    }


async def _evaluate_single_rule(
    rule: dict,
    signal: NormalizedAlarmSignal,
    *,
    camera_uid: str,
    now: datetime,
) -> dict[str, Any]:
    rule_id = str(rule["_id"])
    if not rule.get("enabled"):
        return {
            "rule_id": rule_id,
            "status": RULE_STATUS_SUPPRESSED,
            "reason": "disabled",
        }

    claimed = await try_claim_rule_execution(rule, now=now)
    if not claimed:
        return {
            "rule_id": rule_id,
            "status": RULE_STATUS_SUPPRESSED,
            "reason": "cooldown",
        }

    try:
        return await _execute_rule_actions(rule, signal, camera_uid=camera_uid)
    except (EventValidationError, AlarmSignalValidationError) as exc:
        logger.warning("[alarm-evaluator] rule %s action failed: %s", rule_id, exc)
        return {
            "rule_id": rule_id,
            "status": RULE_STATUS_FAILED,
            "error": str(exc),
        }
    except Exception as exc:
        logger.error("[alarm-evaluator] rule %s unexpected failure: %s", rule_id, exc, exc_info=True)
        return {
            "rule_id": rule_id,
            "status": RULE_STATUS_FAILED,
            "error": "action_failed",
        }


async def process_alarm_signal(raw_signal: dict | NormalizedAlarmSignal) -> dict[str, Any]:
    """Evaluate all enabled rules for a normalized internal alarm signal."""
    signal = raw_signal if isinstance(raw_signal, NormalizedAlarmSignal) else normalize_alarm_signal(raw_signal)

    camera_doc = await get_camera_by_ref(signal.camera_id)
    if not camera_doc:
        raise AlarmSignalValidationError("Camera not found")

    camera_uid = await _resolve_camera_uid(signal, camera_doc)
    rules = await find_matching_enabled_rules(signal)
    now = signal.occurred_at or _utcnow()

    rule_results: list[dict[str, Any]] = []
    events_created: list[str] = []
    triggered = 0
    suppressed = 0
    failed = 0

    for rule in rules:
        result = await _evaluate_single_rule(rule, signal, camera_uid=camera_uid, now=now)
        rule_results.append(result)
        status = result.get("status")
        if status == RULE_STATUS_TRIGGERED:
            triggered += 1
            event_id = result.get("event_id")
            if event_id:
                events_created.append(str(event_id))
        elif status == RULE_STATUS_SUPPRESSED:
            suppressed += 1
        elif status == RULE_STATUS_FAILED:
            failed += 1

    return {
        "camera_id": signal.camera_id,
        "source_type": signal.source_type,
        "matched_rules": len(rules),
        "triggered_rules": triggered,
        "suppressed_rules": suppressed,
        "failed_rules": failed,
        "rule_results": rule_results,
        "events_created": events_created,
    }


async def process_test_alarm_signal(raw_signal: dict) -> dict[str, Any]:
    """Test/script helper — synthetic signal into the evaluator. Not an HTTP API."""
    return await process_alarm_signal(raw_signal)
