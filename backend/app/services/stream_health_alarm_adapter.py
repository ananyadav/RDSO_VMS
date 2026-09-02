"""Bridge confirmed stream-health alarms into the internal rule evaluator."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.alarm_rule_evaluator import process_alarm_signal
from app.services.audit_service import redact_value
from app.services.camera_uid import make_camera_uid
from app.services.event_service import sanitize_event_metadata
from app.services.rtsp_utils import mask_rtsp_url

logger = logging.getLogger(__name__)

ALARM_PROCESS_TIMEOUT_SECONDS = max(
    3, int(os.getenv("STREAM_HEALTH_ALARM_TIMEOUT_SECONDS", "10"))
)

SIGNAL_LOSS_TITLE = "Camera signal lost"
SIGNAL_LOSS_SOURCE = "signal_loss"
_RTSP_EMBEDDED = re.compile(r"(rtsp://)([^/@\s]+):([^/@\s]+)@", re.IGNORECASE)


def previous_alarm_from_camera(camera: dict) -> bool:
    """Persisted alarm flag before the current health update."""
    return bool(camera.get("stream_health_alarm"))


def current_confirmed_alarm(result: dict) -> bool:
    """True only when the health probe result is a confirmed alarm (not suspect)."""
    return bool(result.get("alarm")) and not bool(result.get("ok"))


def is_signal_loss_transition(previous_alarm: bool, current_result: dict) -> bool:
    """Emit only on false → true confirmed-offline transition."""
    return not previous_alarm and current_confirmed_alarm(current_result)


def _parse_checked_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_health_message(result: dict) -> str:
    raw = str(result.get("message") or "").strip()
    if not raw:
        return "No video frame received"
    safe = redact_value("message", raw)
    if not isinstance(safe, str):
        safe = str(safe)
    if "rtsp://" in safe.lower():
        safe = mask_rtsp_url(_RTSP_EMBEDDED.sub(r"\1[REDACTED]@", safe))
    return safe[:2000]


def build_signal_loss_signal(camera: dict, result: dict) -> dict:
    """Normalized internal alarm signal from a confirmed stream-health outcome."""
    cid = str(camera.get("_id") or result.get("cameraId") or "")
    ip = (camera.get("ip_address") or "").strip()
    uid = (
        str(result.get("cameraUid") or camera.get("camera_uid") or "").strip()
        or make_camera_uid(ip)
        or cid
    )
    checked_at = _parse_checked_at(result.get("checkedAt"))
    category = str(result.get("category") or "offline")
    strikes = int(result.get("strikes") or 0)

    return {
        "camera_id": cid,
        "camera_uid": uid,
        "source_type": SIGNAL_LOSS_SOURCE,
        "occurred_at": checked_at.isoformat(),
        "title": SIGNAL_LOSS_TITLE,
        "message": _safe_health_message(result),
        "metadata": sanitize_event_metadata(
            {
                "health_category": category,
                "strikes": strikes,
                "checked_at": checked_at.isoformat(),
            }
        ),
    }


async def handle_stream_health_transition(
    camera: dict,
    *,
    previous_alarm: bool,
    current_result: dict,
) -> Optional[dict]:
    """On confirmed offline transition, forward a normalized signal to the evaluator."""
    if not is_signal_loss_transition(previous_alarm, current_result):
        return None

    signal = build_signal_loss_signal(camera, current_result)
    try:
        return await asyncio.wait_for(
            process_alarm_signal(signal),
            timeout=ALARM_PROCESS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[stream-health-alarm] evaluator timed out for camera %s",
            signal.get("camera_id"),
        )
    except Exception as exc:
        logger.warning(
            "[stream-health-alarm] evaluator failed for camera %s: %s",
            signal.get("camera_id"),
            exc,
        )
    return None


async def notify_stream_health_alarm_transition(
    camera: dict,
    *,
    previous_alarm: bool,
    result: dict,
) -> Optional[dict]:
    """Called after stream health is persisted — never raises."""
    try:
        return await handle_stream_health_transition(
            camera,
            previous_alarm=previous_alarm,
            current_result=result,
        )
    except Exception as exc:
        logger.warning(
            "[stream-health-alarm] unexpected adapter failure: %s",
            exc,
        )
        return None
