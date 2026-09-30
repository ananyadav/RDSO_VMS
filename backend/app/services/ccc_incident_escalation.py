"""RDSO 18.6 — CCC responder / RPF escalation (assignment only — no DMR/Tetra).

Automated escalation uses configurable delay, priority, and location/zone
criteria stored on SOP workflows (Mongo hot-reload).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.ccc_incident_service import (
    INCIDENT_STATUSES,
    incidents_collection,
    timeline_entry,
)
from app.services.ccc_sop_service import get_default_escalation_rules

logger = logging.getLogger(__name__)

# Built-in group labels (operators may also use custom group strings).
RESPONDER_GROUPS = ("responder", "RPF", "rpf", "first_responder", "ccc")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def rule_matches_incident(rule: dict[str, Any], doc: dict[str, Any]) -> bool:
    if not rule.get("enabled", True):
        return False
    status = (doc.get("status") or "").lower()
    from_statuses = [s.lower() for s in (rule.get("from_statuses") or [])]
    if from_statuses and status not in from_statuses:
        return False
    # Don't re-escalate closed/resolved unless explicitly listed.
    if status in ("resolved", "closed") and status not in from_statuses:
        return False

    min_pri = int(rule.get("min_priority") or 1)
    if int(doc.get("priority") or 0) < min_pri:
        return False

    prefix = (rule.get("location_prefix") or "").strip().lower()
    if prefix:
        loc = (doc.get("location") or "").strip().lower()
        if not loc.startswith(prefix) and prefix not in loc:
            return False
    return True


async def assign_incident_responder(
    incident_id: str,
    *,
    actor: Optional[dict],
    assignee_group: Optional[str] = None,
    assignee_user_id: Optional[str] = None,
    assignee_user_name: Optional[str] = None,
    reason: str = "manual_redirect",
) -> Optional[dict]:
    """Manual or automated assignment/redirect to responder or RPF group."""
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(incident_id)
    except (InvalidId, TypeError):
        return None
    doc = await incidents_collection.find_one({"_id": oid})
    if not doc:
        return None

    group = (assignee_group or "").strip() or None
    user_id = (assignee_user_id or "").strip() or None
    user_name = (assignee_user_name or "").strip() or None
    new_status = doc.get("status") or "open"
    if new_status in ("open",):
        new_status = "assigned"
    if reason.startswith("auto_escalat"):
        new_status = "escalated"

    entry = timeline_entry(
        entry_type="escalation" if "escalat" in reason else "assignment",
        actor=actor,
        message=reason,
        detail={
            "assignee_group": group,
            "assignee_user_id": user_id,
            "assignee_user_name": user_name,
            "reason": reason,
            "channel": "ccc_assignment_only",
            "dmr_tetra": False,
        },
    )
    now = _utcnow()
    esc = dict(doc.get("escalation") or {})
    if "escalat" in reason:
        esc["last_escalated_at"] = now
        esc["count"] = int(esc.get("count") or 0) + 1
        esc["last_rule"] = reason

    await incidents_collection.update_one(
        {"_id": oid},
        {
            "$set": {
                "assignee_group": group,
                "assignee_user_id": user_id,
                "assignee_user_name": user_name,
                "status": new_status if new_status in INCIDENT_STATUSES else doc.get("status"),
                "updated_at": now,
                "updated_by": entry["actor"],
                "escalation": esc,
            },
            "$push": {"timeline": entry},
        },
    )
    from app.services.ccc_incident_service import incident_to_public

    fresh = await incidents_collection.find_one({"_id": oid})
    return incident_to_public(fresh) if fresh else None


async def process_due_escalations(*, limit: int = 50) -> dict[str, Any]:
    """Apply due timed/priority/location escalation rules from persisted SOP config."""
    rules = await get_default_escalation_rules()
    if not rules:
        return {"processed": 0, "escalated": 0, "rules": 0}

    now = _utcnow()
    cursor = incidents_collection.find(
        {"status": {"$in": ["open", "assigned", "in_progress", "escalated"]}}
    ).limit(max(1, min(int(limit), 200)))

    escalated = 0
    scanned = 0
    async for doc in cursor:
        scanned += 1
        created = _as_utc(doc.get("incident_time")) or _as_utc(doc.get("created_at")) or now
        last = _as_utc((doc.get("escalation") or {}).get("last_escalated_at"))
        baseline = last or created

        for rule in rules:
            if not rule_matches_incident(rule, doc):
                continue
            delay = int(rule.get("delay_seconds") or 0)
            due_at = baseline + timedelta(seconds=delay)
            if now < due_at:
                continue
            # Avoid re-applying the same rule immediately.
            last_rule = (doc.get("escalation") or {}).get("last_rule") or ""
            rule_tag = f"auto_escalation:{rule.get('id')}"
            if last_rule == rule_tag and last and (now - last) < timedelta(seconds=max(delay, 30)):
                continue

            await assign_incident_responder(
                str(doc["_id"]),
                actor={"id": "system", "name": "CCC Escalation", "role": "system"},
                assignee_group=rule.get("assign_group") or "RPF",
                assignee_user_id=rule.get("assign_user_id"),
                reason=rule_tag,
            )
            escalated += 1
            break

    return {
        "processed": scanned,
        "escalated": escalated,
        "rules": len(rules),
        "dmr_tetra": False,
        "channel": "ccc_assignment_only",
    }
