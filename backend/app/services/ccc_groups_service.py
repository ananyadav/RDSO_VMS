"""RDSO 18.6 — lightweight CCC operational groups (not RBAC roles).

Used for incident/responder/RPF assignment and communication recipients.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database

groups_collection = database.get_collection("ccc_groups")

GROUP_KINDS = ("responder", "rpf", "ops", "communications", "custom")


class CccGroupError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def group_to_public(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "kind": doc.get("kind") or "custom",
        "member_user_ids": list(doc.get("member_user_ids") or []),
        "description": doc.get("description") or "",
        "enabled": bool(doc.get("enabled", True)),
        "not_rbac_role": True,
        "created_at": doc.get("created_at").isoformat()
        if isinstance(doc.get("created_at"), datetime)
        else doc.get("created_at"),
        "updated_at": doc.get("updated_at").isoformat()
        if isinstance(doc.get("updated_at"), datetime)
        else doc.get("updated_at"),
    }


async def ensure_ccc_group_indexes() -> None:
    try:
        await groups_collection.create_index("name", unique=True, name="idx_ccc_group_name")
        await groups_collection.create_index("kind", name="idx_ccc_group_kind")
    except Exception:
        pass


async def list_ccc_groups(*, kind: Optional[str] = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"enabled": True}
    if kind:
        query["kind"] = kind.strip().lower()
    cursor = groups_collection.find(query).sort("name", 1)
    return [group_to_public(d) async for d in cursor]


async def create_ccc_group(
    *,
    name: str,
    kind: str = "custom",
    member_user_ids: Optional[list[str]] = None,
    description: str = "",
) -> dict[str, Any]:
    name_s = (name or "").strip()
    if not name_s:
        raise CccGroupError("name required")
    kind_s = (kind or "custom").strip().lower()
    if kind_s not in GROUP_KINDS:
        kind_s = "custom"
    now = _utcnow()
    doc = {
        "name": name_s,
        "kind": kind_s,
        "member_user_ids": [str(x).strip() for x in (member_user_ids or []) if str(x).strip()],
        "description": (description or "").strip(),
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await groups_collection.insert_one(doc)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or getattr(exc, "code", None) == 11000:
            raise CccGroupError(f"Group already exists: {name_s}") from exc
        raise
    doc["_id"] = result.inserted_id
    return group_to_public(doc)


async def update_ccc_group(group_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        oid = ObjectId(group_id)
    except (InvalidId, TypeError) as exc:
        raise CccGroupError("Invalid group id") from exc
    doc = await groups_collection.find_one({"_id": oid})
    if not doc:
        raise CccGroupError("Group not found")
    updates: dict[str, Any] = {"updated_at": _utcnow()}
    if "name" in patch and patch["name"] is not None:
        name_s = str(patch["name"]).strip()
        if not name_s:
            raise CccGroupError("name cannot be empty")
        updates["name"] = name_s
    if "kind" in patch and patch["kind"] is not None:
        kind_s = str(patch["kind"]).strip().lower()
        updates["kind"] = kind_s if kind_s in GROUP_KINDS else "custom"
    if "member_user_ids" in patch and isinstance(patch["member_user_ids"], list):
        updates["member_user_ids"] = [
            str(x).strip() for x in patch["member_user_ids"] if str(x).strip()
        ]
    if "description" in patch and patch["description"] is not None:
        updates["description"] = str(patch["description"]).strip()
    if "enabled" in patch and patch["enabled"] is not None:
        updates["enabled"] = bool(patch["enabled"])
    await groups_collection.update_one({"_id": oid}, {"$set": updates})
    fresh = await groups_collection.find_one({"_id": oid})
    assert fresh is not None
    return group_to_public(fresh)
