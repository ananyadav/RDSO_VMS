"""Recording fault tolerance helpers — backoff + recovery status persistence.

Used by VideoRecorder when FFmpeg exits unexpectedly (crash or network/camera loss).
Does not implement camera edge SD-card backfill itself — see
``edge_backfill_service`` (RDSO 18.3.16).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import update_recording_session
from app.services.recording_config import (
    RECORDING_RESTART_BASE_SECONDS,
    RECORDING_RESTART_MAX_SECONDS,
)

logger = logging.getLogger(__name__)

RECOVERY_RECORDING = "recording"
RECOVERY_RECONNECTING = "reconnecting"
RECOVERY_BACKOFF = "backoff"


def compute_restart_backoff_seconds(failure_count: int) -> float:
    """Exponential backoff capped at RECORDING_RESTART_MAX_SECONDS.

    failure_count is 1-based (first failure → base delay).
    """
    n = max(1, int(failure_count))
    base = max(0.5, float(RECORDING_RESTART_BASE_SECONDS))
    cap = max(base, float(RECORDING_RESTART_MAX_SECONDS))
    delay = base * (2 ** (n - 1))
    return float(min(cap, delay))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def persist_recording_recovery_status(
    session_id: str,
    *,
    recovery_state: str,
    restart_count: Optional[int] = None,
    last_failure_reason: Optional[str] = None,
    ffmpeg_alive: Optional[bool] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Best-effort Mongo update for operators / health UI."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    payload: dict[str, Any] = {
        "recovery_state": recovery_state,
        "recovery_updated_at": _iso_now(),
    }
    if restart_count is not None:
        payload["restart_count"] = int(restart_count)
    if last_failure_reason is not None:
        payload["last_failure_reason"] = str(last_failure_reason)[:500]
        payload["last_failure_at"] = _iso_now()
    if recovery_state == RECOVERY_RECORDING:
        payload["last_recovered_at"] = _iso_now()
    if ffmpeg_alive is not None:
        payload["ffmpeg_alive"] = bool(ffmpeg_alive)
    if extra:
        payload.update(extra)
    try:
        await update_recording_session(sid, payload)
    except Exception as exc:
        logger.warning(
            "[RECORDING] Failed to persist recovery status session=%s: %s",
            sid,
            exc,
        )
