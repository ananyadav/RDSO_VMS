"""RDSO 18.6.22.15 — CCC pre-emption policy (priority 1–5, distinct from RBAC).

Higher numeric priority may pre-empt lower-priority shared CCC control.
Equal/lower follows deterministic configured policy. Does not invent RBAC roles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import database
from app.services.priority_levels import (
    PRIORITY_MAX,
    PRIORITY_MIN,
    normalize_priority,
    should_auto_display_alarm,
)

_settings = database.get_collection("system_settings")
_SETTINGS_ID = "ccc_preemption"

EQUAL_POLICIES = frozenset({"incoming_wins", "holder_wins"})
SCOPES = frozenset({"live_display", "ccc_control", "ptz_shared"})

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": True,
    "equal_priority": "incoming_wins",
    "min_priority_to_preempt": PRIORITY_MIN,
    "scopes": {
        "live_display": True,
        "ccc_control": True,
        "ptz_shared": False,  # PTZ backend untouched; policy reserved / disabled by default
    },
    "distinct_from_rbac": True,
    "note": (
        "Permissions (RBAC) gate who may act; priority 1–5 decides pre-emption "
        "among authorized operators. Higher number = higher priority."
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_preemption_policy() -> dict[str, Any]:
    doc = await _settings.find_one({"_id": _SETTINGS_ID})
    if not doc:
        return {
            **DEFAULT_POLICY,
            "updated_at": None,
            "rdso_18_6_22_15": True,
        }
    scopes = dict(DEFAULT_POLICY["scopes"])
    if isinstance(doc.get("scopes"), dict):
        for k, v in doc["scopes"].items():
            if k in SCOPES:
                scopes[k] = bool(v)
    equal = str(doc.get("equal_priority") or DEFAULT_POLICY["equal_priority"])
    if equal not in EQUAL_POLICIES:
        equal = "incoming_wins"
    return {
        "enabled": bool(doc.get("enabled", True)),
        "equal_priority": equal,
        "min_priority_to_preempt": int(
            doc.get("min_priority_to_preempt") or PRIORITY_MIN
        ),
        "scopes": scopes,
        "distinct_from_rbac": True,
        "note": DEFAULT_POLICY["note"],
        "updated_at": doc.get("updated_at").isoformat()
        if isinstance(doc.get("updated_at"), datetime)
        else doc.get("updated_at"),
        "rdso_18_6_22_15": True,
    }


async def save_preemption_policy(patch: dict[str, Any]) -> dict[str, Any]:
    current = await get_preemption_policy()
    enabled = (
        bool(patch["enabled"]) if "enabled" in patch and patch["enabled"] is not None else current["enabled"]
    )
    equal = str(patch.get("equal_priority") or current["equal_priority"]).strip().lower()
    if equal not in EQUAL_POLICIES:
        raise ValueError(f"equal_priority must be one of {sorted(EQUAL_POLICIES)}")
    min_p = normalize_priority(
        patch.get("min_priority_to_preempt", current["min_priority_to_preempt"]),
        default=PRIORITY_MIN,
    )
    scopes = dict(current["scopes"])
    if isinstance(patch.get("scopes"), dict):
        for k, v in patch["scopes"].items():
            if k in SCOPES:
                scopes[k] = bool(v)
    now = _utcnow()
    await _settings.update_one(
        {"_id": _SETTINGS_ID},
        {
            "$set": {
                "_id": _SETTINGS_ID,
                "enabled": enabled,
                "equal_priority": equal,
                "min_priority_to_preempt": min_p,
                "scopes": scopes,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    return await get_preemption_policy()


def evaluate_preemption(
    *,
    actor_priority: int,
    holder_priority: int,
    scope: str = "ccc_control",
    policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Deterministic pre-emption decision. Permissions must already authorize actor."""
    pol = policy or DEFAULT_POLICY
    scope_s = str(scope or "ccc_control").strip().lower()
    actor = int(actor_priority)
    holder = int(holder_priority)
    scopes = pol.get("scopes") or {}
    if not pol.get("enabled", True):
        return {
            "preempt": False,
            "reason": "preemption_disabled",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    if scope_s in scopes and not scopes.get(scope_s, True):
        return {
            "preempt": False,
            "reason": "scope_disabled",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    min_p = int(pol.get("min_priority_to_preempt") or PRIORITY_MIN)
    if actor < min_p:
        return {
            "preempt": False,
            "reason": "below_min_priority_to_preempt",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    if actor > holder:
        return {
            "preempt": True,
            "reason": "higher_priority",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    if actor < holder:
        return {
            "preempt": False,
            "reason": "lower_priority",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    # Equal priority
    equal = str(pol.get("equal_priority") or "incoming_wins")
    if equal == "incoming_wins":
        return {
            "preempt": True,
            "reason": "equal_incoming_wins",
            "actor_priority": actor,
            "holder_priority": holder,
            "scope": scope_s,
        }
    return {
        "preempt": False,
        "reason": "equal_holder_wins",
        "actor_priority": actor,
        "holder_priority": holder,
        "scope": scope_s,
    }


def live_display_preempts(*, alarm_priority: int, user_priority: int, policy: Optional[dict] = None) -> bool:
    """Reuse existing 18.1.27 rule when scope live_display enabled; else never auto-steal."""
    pol = policy or DEFAULT_POLICY
    if not pol.get("enabled", True):
        return False
    scopes = pol.get("scopes") or {}
    if not scopes.get("live_display", True):
        return False
    return should_auto_display_alarm(
        alarm_priority=alarm_priority, user_priority=user_priority
    )


def admin_gui_options_public() -> dict[str, Any]:
    """Point admins at existing CCC GUI configuration surfaces (no duplicate RBAC)."""
    return {
        "rdso_18_6_22_15": True,
        "reuses_existing_rbac": True,
        "user_management_path": "/user-management",
        "permissions": ["Events", "Live View", "recording.view", "System"],
        "priority_levels": {"min": PRIORITY_MIN, "max": PRIORITY_MAX, "distinct_from_rbac": True},
        "ccc_gui": [
            {"id": "dashboard_prefs", "path": "/api/ccc/dashboard/prefs", "ui": "/ccc?tab=overview"},
            {"id": "ops_groups", "path": "/api/ccc/groups", "ui": "/ccc?tab=status"},
            {"id": "message_templates", "path": "/api/ccc/message-templates", "ui": "/ccc?tab=status"},
            {"id": "sop_workflows", "path": "/api/ccc/sop-workflows", "ui": "/ccc?tab=incidents"},
            {"id": "devices", "path": "/api/ccc/devices", "ui": "/ccc?tab=devices"},
            {"id": "preemption", "path": "/api/ccc/admin/preemption-policy", "ui": "/ccc?tab=devices"},
        ],
    }
