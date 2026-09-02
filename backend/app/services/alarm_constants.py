"""Alarm rule and event constants — extensible source types, no AI types."""

from __future__ import annotations

SOURCE_TYPES = frozenset(
    {
        "signal_loss",
        "motion",
        "digital_input",
        "recording_failure",
        "manual_test",
    }
)

SEVERITIES = frozenset({"info", "warning", "critical"})

RULE_ACTIONS = frozenset({"create_event", "ui_notification", "start_recording"})

RECORDING_DURATION_MIN_SECONDS = 5
RECORDING_DURATION_MAX_SECONDS = 3600
RECORDING_DURATION_DEFAULT_SECONDS = 60

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
