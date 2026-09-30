"""RDSO 18.1.27 — numeric priority levels (distinct from RBAC roles / permissions).

Levels 1–5 where **5 is highest**. Severity (info/warning/critical) is preserved
and used as a secondary sort key + default priority when unset.
"""

from __future__ import annotations

from typing import Any, Optional

PRIORITY_MIN = 1
PRIORITY_MAX = 5
PRIORITY_LEVELS = tuple(range(PRIORITY_MIN, PRIORITY_MAX + 1))
DEFAULT_USER_PRIORITY = 1  # see all auto-displays (safest default)
DEFAULT_ALARM_PRIORITY = 3

# Map legacy severity → default numeric priority when rule/event omits priority.
SEVERITY_DEFAULT_PRIORITY = {
    "critical": 5,
    "warning": 3,
    "info": 1,
}

SEVERITY_RANK = {
    "critical": 3,
    "warning": 2,
    "info": 1,
}


class PriorityValidationError(ValueError):
    pass


def normalize_priority(raw: Any, *, default: int = DEFAULT_ALARM_PRIORITY) -> int:
    if raw is None or raw == "":
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise PriorityValidationError(
            f"priority must be an integer {PRIORITY_MIN}–{PRIORITY_MAX}"
        ) from exc
    if value < PRIORITY_MIN or value > PRIORITY_MAX:
        raise PriorityValidationError(
            f"priority must be between {PRIORITY_MIN} and {PRIORITY_MAX}"
        )
    return value


def priority_from_severity(severity: str | None) -> int:
    key = str(severity or "").strip().lower()
    return int(SEVERITY_DEFAULT_PRIORITY.get(key, DEFAULT_ALARM_PRIORITY))


def resolve_alarm_priority(
    *,
    priority: Any = None,
    severity: str | None = None,
) -> int:
    """Explicit priority wins; otherwise derive from severity."""
    if priority is None or priority == "":
        return priority_from_severity(severity)
    return normalize_priority(priority, default=priority_from_severity(severity))


def severity_rank(severity: str | None) -> int:
    return int(SEVERITY_RANK.get(str(severity or "").strip().lower(), 0))


def alarm_sort_key(item: dict[str, Any]) -> tuple:
    """Higher priority / severity / newer first → sort reverse=True."""
    return (
        int(item.get("priority") or priority_from_severity(item.get("severity"))),
        severity_rank(item.get("severity")),
        str(item.get("occurred_at") or ""),
    )


def sort_alarms_by_priority(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=alarm_sort_key, reverse=True)


def should_auto_display_alarm(*, alarm_priority: int, user_priority: int) -> bool:
    """Deterministic user↔alarm conflict rule (RDSO 18.1.27).

    Higher numeric value = more important.
    Auto-steal Live View fullscreen only when alarm_priority >= user_priority.
    Equal priorities: alarm wins (safety). Lower alarms remain in notifications/queue
    but do not interrupt a higher-priority operator's current view.
    """
    return int(alarm_priority) >= int(user_priority)


def priority_policy_public() -> dict[str, Any]:
    return {
        "min": PRIORITY_MIN,
        "max": PRIORITY_MAX,
        "levels": list(PRIORITY_LEVELS),
        "highest_is": PRIORITY_MAX,
        "default_user_priority": DEFAULT_USER_PRIORITY,
        "default_alarm_priority": DEFAULT_ALARM_PRIORITY,
        "severity_defaults": dict(SEVERITY_DEFAULT_PRIORITY),
        "conflict_rule": (
            "Auto-display fullscreen when alarm.priority >= user.priority; "
            "otherwise queue/notify only. Alarm queue sorts by priority desc, "
            "then severity desc, then occurred_at desc. Distinct from RBAC roles."
        ),
        "distinct_from_rbac": True,
    }
