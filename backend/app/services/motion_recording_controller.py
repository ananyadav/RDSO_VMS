"""Motion/activity recording controller — dual-stream switch with debounce (RDSO 18.3.14).

Does not implement a software motion detector. Activity signals come from camera
ISAPI polls and/or normalized alarm motion signals. Stream switches are cooldown-
guarded and never create a second concurrent recorder for the same camera.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.motion_recording_types import (
    ACTIVITY_ACTIVE,
    ACTIVITY_IDLE,
    public_motion_config,
)

logger = logging.getLogger(__name__)

# camera_id -> runtime state
_STATE: dict[str, dict[str, Any]] = {}
_SWITCH_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(camera_id: str) -> asyncio.Lock:
    if camera_id not in _SWITCH_LOCKS:
        _SWITCH_LOCKS[camera_id] = asyncio.Lock()
    return _SWITCH_LOCKS[camera_id]


def get_stream_override(camera_id: str | None) -> Optional[str]:
    """Used by resolve_recording_stream_choice while motion mode is active."""
    if not camera_id:
        return None
    st = _STATE.get(str(camera_id))
    if not st or not st.get("enabled"):
        return None
    # Prefer desired (policy) so activity elevates quality even before restart finishes.
    for key in ("desired_stream", "current_stream"):
        stream = st.get(key)
        if stream in ("main", "sub"):
            return stream
    return None


def get_activity_state(camera_id: str) -> dict[str, Any]:
    st = _STATE.get(str(camera_id)) or {}
    return {
        "camera_id": str(camera_id),
        "enabled": bool(st.get("enabled")),
        "activity": st.get("activity") or ACTIVITY_IDLE,
        "current_stream": st.get("current_stream"),
        "desired_stream": st.get("desired_stream"),
        "last_motion_at": st.get("last_motion_at"),
        "last_switch_at": st.get("last_switch_at"),
        "last_error": st.get("last_error"),
        "hold_seconds": st.get("hold_seconds"),
        "cooldown_seconds": st.get("cooldown_seconds"),
        "last_transition": st.get("last_transition"),
        "transitions": list(st.get("transitions") or [])[-10:],
        "bitrate_compliance": st.get("bitrate_compliance"),
    }


async def refresh_camera_config(camera_id: str, cfg: dict[str, Any] | None = None) -> None:
    cfg = public_motion_config(cfg)
    st = _STATE.setdefault(str(camera_id), {})
    st["enabled"] = bool(cfg.get("enabled"))
    st["active_stream"] = cfg["active_stream"]
    st["idle_stream"] = cfg["idle_stream"]
    st["hold_seconds"] = float(cfg["hold_seconds"])
    st["cooldown_seconds"] = float(cfg["cooldown_seconds"])
    if "activity" not in st:
        st["activity"] = ACTIVITY_IDLE
    if "current_stream" not in st:
        st["current_stream"] = cfg["idle_stream"] if cfg["enabled"] else None
    st["desired_stream"] = (
        cfg["active_stream"] if st.get("activity") == ACTIVITY_ACTIVE else cfg["idle_stream"]
    )
    if not cfg["enabled"]:
        st["current_stream"] = None
        st["desired_stream"] = None


async def notify_motion_activity(
    camera_id: str,
    *,
    active: bool,
    source: str = "signal",
) -> dict[str, Any]:
    """Record a motion activity edge and apply stream policy when enabled."""
    camera_id = str(camera_id)
    st = _STATE.setdefault(camera_id, {})
    if not st.get("enabled"):
        # Lazy-load config from Mongo
        try:
            from app.services.motion_recording_config import get_motion_recording_settings

            settings = await get_motion_recording_settings(camera_id)
            await refresh_camera_config(camera_id, settings.get("config"))
            st = _STATE.get(camera_id) or {}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "switched": False}

    if not st.get("enabled"):
        return {
            "ok": True,
            "ignored": True,
            "reason": "motion_recording_disabled",
            "switched": False,
            "state": get_activity_state(camera_id),
        }

    now = time.time()
    if active:
        st["last_motion_at"] = now
        st["activity"] = ACTIVITY_ACTIVE
        st["desired_stream"] = st.get("active_stream") or "main"
    else:
        # Explicit idle notification — still respect hold window from last motion
        last = float(st.get("last_motion_at") or 0)
        hold = float(st.get("hold_seconds") or 30)
        if last and (now - last) < hold:
            st["desired_stream"] = st.get("active_stream") or "main"
            st["activity"] = ACTIVITY_ACTIVE
        else:
            st["activity"] = ACTIVITY_IDLE
            st["desired_stream"] = st.get("idle_stream") or "sub"

    st["last_source"] = source
    switched = await _maybe_switch_stream(camera_id)
    return {
        "ok": True,
        "switched": switched,
        "source": source,
        "state": get_activity_state(camera_id),
    }


async def tick_idle_hold(camera_id: str) -> dict[str, Any]:
    """Periodic: after hold expires without motion, return to idle stream."""
    camera_id = str(camera_id)
    st = _STATE.get(camera_id)
    if not st or not st.get("enabled"):
        return {"ok": True, "switched": False}
    if st.get("activity") != ACTIVITY_ACTIVE:
        return {"ok": True, "switched": False}
    last = float(st.get("last_motion_at") or 0)
    hold = float(st.get("hold_seconds") or 30)
    if not last or (time.time() - last) < hold:
        return {"ok": True, "switched": False}
    st["activity"] = ACTIVITY_IDLE
    st["desired_stream"] = st.get("idle_stream") or "sub"
    switched = await _maybe_switch_stream(camera_id)
    return {"ok": True, "switched": switched, "state": get_activity_state(camera_id)}


async def _maybe_switch_stream(camera_id: str) -> bool:
    """Switch recording RTSP stream if desired != current, respecting cooldown."""
    async with _lock_for(camera_id):
        st = _STATE.get(camera_id) or {}
        if not st.get("enabled"):
            return False
        desired = st.get("desired_stream")
        current = st.get("current_stream")
        if desired not in ("main", "sub"):
            return False
        if desired == current:
            return False

        cooldown = float(st.get("cooldown_seconds") or 15)
        last_sw = float(st.get("last_switch_at") or 0)
        if last_sw and (time.time() - last_sw) < cooldown:
            st["last_error"] = "cooldown_active"
            logger.info(
                "[MOTION] Skip stream switch camera=%s desired=%s (cooldown)",
                camera_id,
                desired,
            )
            return False

        from app.services.video_recording import (
            ACTIVE_RECORDINGS,
            is_camera_recording,
            start_camera_recording,
            stop_camera_recording,
        )
        from app.services.recording_config import is_recording_engine_enabled

        if not is_recording_engine_enabled():
            # Still update desired/current intent for next start
            st["current_stream"] = desired
            st["last_switch_at"] = time.time()
            return False

        recording = await is_camera_recording(camera_id)
        if not recording and camera_id not in ACTIVE_RECORDINGS:
            # Not currently recording — just set override for next start
            st["current_stream"] = desired
            st["last_switch_at"] = time.time()
            st["last_error"] = None
            return False

        # Restart single recorder onto the new stream (no duplicate slot)
        t0 = time.perf_counter()
        from_stream = current
        try:
            st["current_stream"] = desired  # override before restart
            await stop_camera_recording(camera_id)
            # Clear ownership slot; refuse start if a recorder entry remains
            ACTIVE_RECORDINGS.pop(camera_id, None)
            if camera_id in ACTIVE_RECORDINGS:
                st["last_error"] = "duplicate_recorder_prevented"
                logger.error(
                    "[MOTION] Refusing start; ACTIVE_RECORDINGS still occupied camera=%s",
                    camera_id,
                )
                return False
            await start_camera_recording(camera_id)
            # If start somehow double-registered, leave a marker (should be exactly one)
            if sum(1 for k in ACTIVE_RECORDINGS if k == camera_id) > 1:
                st["last_error"] = "duplicate_recorder_detected"
                logger.error("[MOTION] Duplicate ACTIVE_RECORDINGS for camera=%s", camera_id)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            st["last_switch_at"] = time.time()
            st["last_error"] = None if not st.get("last_error") else st["last_error"]
            if st.get("last_error") == "duplicate_recorder_detected":
                return False
            transition = {
                "from_stream": from_stream,
                "to_stream": desired,
                "direction": (
                    "quiet_to_motion"
                    if desired == (st.get("active_stream") or "main")
                    else "motion_to_idle"
                ),
                "elapsed_ms": elapsed_ms,
                "at": st["last_switch_at"],
            }
            st["last_transition"] = transition
            hist = list(st.get("transitions") or [])
            hist.append(transition)
            st["transitions"] = hist[-20:]
            logger.info(
                "[MOTION] Switched recording stream camera=%s %s->%s in %.1fms",
                camera_id,
                from_stream,
                desired,
                elapsed_ms,
            )
            return True
        except Exception as exc:
            st["last_error"] = str(exc)
            logger.warning(
                "[MOTION] Stream switch failed camera=%s: %s", camera_id, exc
            )
            return False


def clear_motion_state(camera_id: str | None = None) -> None:
    if camera_id:
        _STATE.pop(str(camera_id), None)
    else:
        _STATE.clear()
