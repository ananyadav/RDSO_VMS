"""Instant Replay configuration (env-backed, validated)."""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, *, min_v: int, max_v: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(min_v, min(max_v, value))


# Master switch — default on so IR works when recording is off (bounded buffer).
INSTANT_REPLAY_ENABLED = _env_bool("INSTANT_REPLAY_ENABLED", True)

# Rolling buffer history window (seconds).
INSTANT_REPLAY_BUFFER_SECONDS = _env_int(
    "INSTANT_REPLAY_BUFFER_SECONDS", 300, min_v=30, max_v=1800
)

# Short HLS segments for practical recent seek (not permanent recording).
INSTANT_REPLAY_SEGMENT_SECONDS = _env_int(
    "INSTANT_REPLAY_SEGMENT_SECONDS", 2, min_v=1, max_v=10
)

# Stop buffer after last consumer disappears.
INSTANT_REPLAY_IDLE_GRACE_SECONDS = _env_int(
    "INSTANT_REPLAY_IDLE_GRACE_SECONDS", 45, min_v=10, max_v=600
)

# Lease heartbeat TTL — consumer must refresh within this window.
INSTANT_REPLAY_LEASE_TTL_SECONDS = _env_int(
    "INSTANT_REPLAY_LEASE_TTL_SECONDS", 60, min_v=20, max_v=300
)

# Max go-back presets (seconds) offered to UI.
INSTANT_REPLAY_PRESETS_SECONDS = (10, 30, 60, 300)


def permanent_recording_segment_seconds() -> float:
    """Permanent archive HLS segment length — used only for IR source selection."""
    try:
        from app.services.recording_config import RECORDING_SEGMENT_SECONDS

        return max(1.0, float(RECORDING_SEGMENT_SECONDS))
    except Exception:
        return 300.0


def permanent_hls_reliable_for_lookback(seconds_ago: float) -> bool:
    """
    Long permanent HLS segments cannot reliably serve very recent lookbacks
    (content may still be inside the open/incomplete segment).
    Prefer the short Instant Replay buffer for those targets.
    """
    return float(seconds_ago) >= permanent_recording_segment_seconds()


def instant_replay_public_config() -> dict:
    return {
        "enabled": INSTANT_REPLAY_ENABLED,
        "bufferSeconds": INSTANT_REPLAY_BUFFER_SECONDS,
        "segmentSeconds": INSTANT_REPLAY_SEGMENT_SECONDS,
        "idleGraceSeconds": INSTANT_REPLAY_IDLE_GRACE_SECONDS,
        "presetsSeconds": list(INSTANT_REPLAY_PRESETS_SECONDS),
        "permanentSegmentSeconds": permanent_recording_segment_seconds(),
    }
