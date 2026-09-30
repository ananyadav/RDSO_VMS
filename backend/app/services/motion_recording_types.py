"""RDSO 18.3.14 — motion/activity-based recording types."""

from __future__ import annotations

from typing import Any

ACTIVITY_IDLE = "idle"
ACTIVITY_ACTIVE = "active"

DEFAULT_HOLD_SECONDS = 30.0
DEFAULT_COOLDOWN_SECONDS = 15.0
DEFAULT_POLL_SECONDS = 5.0
MIN_HOLD_SECONDS = 5.0
MAX_HOLD_SECONDS = 600.0
MIN_COOLDOWN_SECONDS = 5.0
MAX_COOLDOWN_SECONDS = 300.0


def clamp_hold(seconds: float | int | None) -> float:
    try:
        v = float(seconds) if seconds is not None else DEFAULT_HOLD_SECONDS
    except (TypeError, ValueError):
        v = DEFAULT_HOLD_SECONDS
    return max(MIN_HOLD_SECONDS, min(MAX_HOLD_SECONDS, v))


def clamp_cooldown(seconds: float | int | None) -> float:
    try:
        v = float(seconds) if seconds is not None else DEFAULT_COOLDOWN_SECONDS
    except (TypeError, ValueError):
        v = DEFAULT_COOLDOWN_SECONDS
    return max(MIN_COOLDOWN_SECONDS, min(MAX_COOLDOWN_SECONDS, v))


def normalize_stream_choice(raw: Any, *, default: str) -> str:
    text = str(raw or "").strip().lower()
    if text in ("main", "sub"):
        return text
    return default


def default_motion_recording_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "active_stream": "main",
        "idle_stream": "sub",
        "hold_seconds": DEFAULT_HOLD_SECONDS,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        "motion_source": "auto",  # auto | isapi | signal | disabled
    }


def public_motion_config(raw: dict | None) -> dict[str, Any]:
    base = default_motion_recording_config()
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled"))
    base["active_stream"] = normalize_stream_choice(
        raw.get("active_stream"), default="main"
    )
    base["idle_stream"] = normalize_stream_choice(
        raw.get("idle_stream"), default="sub"
    )
    if base["active_stream"] == base["idle_stream"]:
        # Keep a meaningful dual-mode pair
        base["idle_stream"] = "sub" if base["active_stream"] == "main" else "main"
    base["hold_seconds"] = clamp_hold(raw.get("hold_seconds"))
    base["cooldown_seconds"] = clamp_cooldown(raw.get("cooldown_seconds"))
    src = str(raw.get("motion_source") or "auto").strip().lower()
    if src not in ("auto", "isapi", "signal", "disabled"):
        src = "auto"
    base["motion_source"] = src
    return base
