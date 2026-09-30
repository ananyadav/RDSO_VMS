"""Alarm rule and event constants — extensible source types, no AI types."""

from __future__ import annotations

SOURCE_TYPES = frozenset(
    {
        "signal_loss",
        "motion",
        "digital_input",
        "recording_failure",
        "manual_test",
        "external_sensor",  # CCC generic sensor ingest (no fake vendors)
    }
)

# External / legacy aliases normalized before validation
SOURCE_TYPE_ALIASES = {
    "video_loss": "signal_loss",
    "videoloss": "signal_loss",
    "relay": "digital_input",
    "relay_input": "digital_input",
    "alarm_input": "digital_input",
    "sensor": "external_sensor",
    "device": "external_sensor",
    "ccc_sensor": "external_sensor",
}

SEVERITIES = frozenset({"info", "warning", "critical"})

# Numeric priority is defined in priority_levels (1–5); severity remains separate.

RULE_ACTIONS = frozenset({"create_event", "ui_notification", "start_recording"})

# RDSO 18.2.28 — optional Live View display switch (with ui_notification)
DISPLAY_LAYOUTS = frozenset({"1x1", "2x2", "3x3", "4x4", "5x5", "6x6"})
DISPLAY_MODES = frozenset({"fullscreen", "layout_switch"})
DISPLAY_MONITOR_MIN = 1
DISPLAY_MONITOR_MAX = 8

RECORDING_DURATION_MIN_SECONDS = 5
RECORDING_DURATION_MAX_SECONDS = 3600
RECORDING_DURATION_DEFAULT_SECONDS = 60

# Pre-alarm lookback (real footage before trigger). 0 = post-only (legacy).
PRE_ALARM_MIN_SECONDS = 0
PRE_ALARM_MAX_SECONDS = 300  # clamped further to Instant Replay buffer window
PRE_ALARM_DEFAULT_SECONDS = 0

# Post-alarm continue-after-trigger window (maps from legacy duration_seconds).
POST_ALARM_MIN_SECONDS = RECORDING_DURATION_MIN_SECONDS
POST_ALARM_MAX_SECONDS = RECORDING_DURATION_MAX_SECONDS
POST_ALARM_DEFAULT_SECONDS = RECORDING_DURATION_DEFAULT_SECONDS

RECORDING_ACTION_STATUSES = frozenset(
    {
        "started",
        "already_recording",
        "extended",
        "engine_disabled",
        "master_disabled",
        "failed",
    }
)

EVENT_STATUSES = frozenset({"open", "acknowledged"})

RULE_NAME_MAX_LEN = 120
COOLDOWN_MIN_SECONDS = 0
COOLDOWN_MAX_SECONDS = 86400
EVENT_METADATA_MAX_KEYS = 32
EVENT_METADATA_MAX_JSON_BYTES = 8192
