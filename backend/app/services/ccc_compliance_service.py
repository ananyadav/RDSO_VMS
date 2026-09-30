"""RDSO 18.6 — practical SOP compliance state for CCC incidents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.ccc_incident_service import incidents_collection
from app.services.ccc_sop_service import get_sop_workflow


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def incident_compliance(doc: dict) -> dict[str, Any]:
    wf_id = doc.get("sop_workflow_id")
    step_index = int(doc.get("sop_step_index") or 0)
    steps: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    if wf_id:
        wf = await get_sop_workflow(str(wf_id))
        if wf:
            steps = list(wf.get("steps") or [])
            rules = list(wf.get("escalation_rules") or [])

    total = len(steps)
    completed = min(max(0, step_index), total) if total else 0
    pending = max(0, total - completed)
    required = [
        {"order": s.get("order"), "title": s.get("title"), "done": i < completed}
        for i, s in enumerate(steps)
    ]

    overdue = False
    due_at = None
    if rules and doc.get("status") not in ("resolved", "closed"):
        due_seconds = int(rules[0].get("delay_seconds") or 0)
        created = _as_utc(doc.get("incident_time")) or _as_utc(doc.get("created_at"))
        if created and due_seconds > 0 and pending > 0:
            due_at = created + timedelta(seconds=due_seconds)
            overdue = datetime.now(timezone.utc) > due_at

    state = "complete" if total and pending == 0 else ("pending" if total else "none")
    if overdue:
        state = "overdue"

    return {
        "sop_workflow_id": wf_id,
        "total_steps": total,
        "completed_steps": completed,
        "pending_steps": pending,
        "steps": required,
        "assigned_user_id": doc.get("assignee_user_id"),
        "assigned_group": doc.get("assignee_group"),
        "due_at": due_at.isoformat() if due_at else None,
        "overdue": overdue,
        "state": state,
    }


async def get_incident_compliance(incident_id: str) -> Optional[dict[str, Any]]:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid = ObjectId(incident_id)
    except (InvalidId, TypeError):
        return None
    doc = await incidents_collection.find_one({"_id": oid})
    if not doc:
        return None
    return await incident_compliance(doc)


async def summarize_compliance() -> dict[str, Any]:
    pending = overdue = complete = none = 0
    cursor = incidents_collection.find({"status": {"$nin": ["closed"]}}).limit(200)
    async for doc in cursor:
        c = await incident_compliance(doc)
        st = c.get("state")
        if st == "overdue":
            overdue += 1
        elif st == "complete":
            complete += 1
        elif st == "pending":
            pending += 1
        else:
            none += 1
    return {
        "pending": pending,
        "overdue": overdue,
        "complete": complete,
        "none": none,
        "scanned_limit": 200,
    }
