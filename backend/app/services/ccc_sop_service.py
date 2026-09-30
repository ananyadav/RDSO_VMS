"""RDSO 18.6 — configurable CCC SOP / workflow templates (hot-reload from Mongo).

Edits take effect on the next read/apply — no application restart/redeploy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database

logger = logging.getLogger(__name__)

sop_collection = database.get_collection("ccc_sop_workflows")


class SopValidationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def ensure_sop_indexes() -> None:
    try:
        await sop_collection.create_index("name", name="idx_ccc_sop_name")
        await sop_collection.create_index("enabled", name="idx_ccc_sop_enabled")
    except Exception as exc:
        logger.warning("[ccc-sop] index setup: %s", exc)


def sop_to_public(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "enabled": bool(doc.get("enabled", True)),
        "steps": list(doc.get("steps") or []),
        "escalation_rules": list(doc.get("escalation_rules") or []),
        "created_at": _iso(doc["created_at"])
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
        "updated_at": _iso(doc["updated_at"])
        if isinstance(doc.get("updated_at"), datetime)
        else doc.get("updated_at"),
        "hot_reload": True,
        "requires_restart": False,
        "rdso_18_6": True,
    }


def _normalize_steps(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise SopValidationError("steps must be a non-empty ordered list")
    steps = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            steps.append({"order": i, "title": text, "instruction": text})
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            steps.append(
                {
                    "order": int(item.get("order", i)),
                    "title": title,
                    "instruction": str(item.get("instruction") or title).strip(),
                }
            )
    if not steps:
        raise SopValidationError("steps must include at least one title")
    steps.sort(key=lambda s: s["order"])
    for i, s in enumerate(steps):
        s["order"] = i
    return steps


def _normalize_escalation_rules(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SopValidationError("escalation_rules must be a list")
    rules = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        delay = int(item.get("delay_seconds") or item.get("delay_sec") or 0)
        if delay < 0:
            raise SopValidationError("delay_seconds must be >= 0")
        min_pri = item.get("min_priority", item.get("priority"))
        try:
            min_pri_i = int(min_pri) if min_pri is not None else 1
        except (TypeError, ValueError) as exc:
            raise SopValidationError("min_priority must be int") from exc
        rules.append(
            {
                "id": str(item.get("id") or f"rule_{i}"),
                "delay_seconds": delay,
                "min_priority": max(1, min(5, min_pri_i)),
                "location_prefix": str(item.get("location_prefix") or item.get("zone") or "").strip(),
                "assign_group": str(
                    item.get("assign_group") or item.get("group") or "responder"
                ).strip()
                or "responder",
                "assign_user_id": item.get("assign_user_id") or None,
                "from_statuses": [
                    str(s).strip().lower()
                    for s in (item.get("from_statuses") or ["open", "assigned", "in_progress"])
                    if str(s).strip()
                ],
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return rules


async def list_sop_workflows(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if enabled_only:
        query["enabled"] = True
    cursor = sop_collection.find(query).sort("name", 1)
    return [sop_to_public(doc) async for doc in cursor]


async def get_sop_workflow(workflow_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(workflow_id)
    except (InvalidId, TypeError):
        return None
    doc = await sop_collection.find_one({"_id": oid})
    return sop_to_public(doc) if doc else None


async def create_sop_workflow(
    *,
    name: str,
    description: str = "",
    steps: Any,
    escalation_rules: Any = None,
    enabled: bool = True,
) -> dict:
    name_s = (name or "").strip()
    if not name_s:
        raise SopValidationError("name is required")
    now = _utcnow()
    doc = {
        "name": name_s,
        "description": (description or "").strip(),
        "enabled": bool(enabled),
        "steps": _normalize_steps(steps),
        "escalation_rules": _normalize_escalation_rules(escalation_rules),
        "created_at": now,
        "updated_at": now,
    }
    result = await sop_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return sop_to_public(doc)


async def update_sop_workflow(workflow_id: str, patch: dict[str, Any]) -> dict:
    try:
        oid = ObjectId(workflow_id)
    except (InvalidId, TypeError) as exc:
        raise SopValidationError("Invalid workflow id") from exc
    doc = await sop_collection.find_one({"_id": oid})
    if not doc:
        raise SopValidationError("Workflow not found")

    updates: dict[str, Any] = {"updated_at": _utcnow()}
    if "name" in patch and patch["name"] is not None:
        name_s = str(patch["name"]).strip()
        if not name_s:
            raise SopValidationError("name cannot be empty")
        updates["name"] = name_s
    if "description" in patch and patch["description"] is not None:
        updates["description"] = str(patch["description"]).strip()
    if "enabled" in patch and patch["enabled"] is not None:
        updates["enabled"] = bool(patch["enabled"])
    if "steps" in patch and patch["steps"] is not None:
        updates["steps"] = _normalize_steps(patch["steps"])
    if "escalation_rules" in patch:
        updates["escalation_rules"] = _normalize_escalation_rules(patch["escalation_rules"])

    await sop_collection.update_one({"_id": oid}, {"$set": updates})
    fresh = await sop_collection.find_one({"_id": oid})
    assert fresh is not None
    return sop_to_public(fresh)


async def delete_sop_workflow(workflow_id: str) -> bool:
    try:
        oid = ObjectId(workflow_id)
    except (InvalidId, TypeError):
        return False
    result = await sop_collection.delete_one({"_id": oid})
    return result.deleted_count > 0


async def get_default_escalation_rules() -> list[dict[str, Any]]:
    """Merge escalation rules from all enabled SOPs (hot-read from Mongo)."""
    rules: list[dict[str, Any]] = []
    for wf in await list_sop_workflows(enabled_only=True):
        for rule in wf.get("escalation_rules") or []:
            if rule.get("enabled", True):
                enriched = dict(rule)
                enriched["workflow_id"] = wf["id"]
                enriched["workflow_name"] = wf["name"]
                rules.append(enriched)
    return rules
