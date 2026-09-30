"""RDSO 18.6 — CCC internal message templates + communications / receipts.

Internal software messaging/audit only — does NOT claim SMS/email/DMR/Tetra delivery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database

templates_collection = database.get_collection("ccc_message_templates")
comms_collection = database.get_collection("ccc_communications")

COMM_STATUSES = ("draft", "sent", "delivered", "acknowledged")


class CccCommsError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _actor(user: Optional[dict]) -> dict[str, str]:
    if not user:
        return {"id": "", "name": "", "role": ""}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("username") or ""),
        "role": str(user.get("role") or ""),
    }


def template_to_public(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name") or "",
        "body": doc.get("body") or "",
        "channel": "ccc_internal",
        "external_delivery": False,
        "dmr_tetra": False,
        "sms_email": False,
        "enabled": bool(doc.get("enabled", True)),
        "updated_at": doc.get("updated_at").isoformat()
        if isinstance(doc.get("updated_at"), datetime)
        else doc.get("updated_at"),
    }


def comm_to_public(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "incident_id": doc.get("incident_id") or "",
        "template_id": doc.get("template_id"),
        "subject": doc.get("subject") or "",
        "body": doc.get("body") or "",
        "recipient_user_id": doc.get("recipient_user_id"),
        "recipient_group": doc.get("recipient_group"),
        "status": doc.get("status") or "sent",
        "sent_at": doc.get("sent_at").isoformat()
        if isinstance(doc.get("sent_at"), datetime)
        else doc.get("sent_at"),
        "delivered_at": doc.get("delivered_at").isoformat()
        if isinstance(doc.get("delivered_at"), datetime)
        else doc.get("delivered_at"),
        "acknowledged_at": doc.get("acknowledged_at").isoformat()
        if isinstance(doc.get("acknowledged_at"), datetime)
        else doc.get("acknowledged_at"),
        "acknowledged_by": doc.get("acknowledged_by"),
        "sender": doc.get("sender") or {},
        "channel": "ccc_internal",
        "external_delivery": False,
        "dmr_tetra": False,
        "receipt_known_in_software": True,
        "note": "Status reflects in-app CCC receipt only — not carrier/DMR delivery",
    }


async def ensure_ccc_comms_indexes() -> None:
    try:
        await templates_collection.create_index("name", name="idx_ccc_msg_tpl_name")
        await comms_collection.create_index("incident_id", name="idx_ccc_comm_incident")
        await comms_collection.create_index("status", name="idx_ccc_comm_status")
    except Exception:
        pass


async def list_message_templates() -> list[dict[str, Any]]:
    cursor = templates_collection.find({"enabled": True}).sort("name", 1)
    return [template_to_public(d) async for d in cursor]


async def create_message_template(*, name: str, body: str) -> dict[str, Any]:
    name_s = (name or "").strip()
    body_s = (body or "").strip()
    if not name_s or not body_s:
        raise CccCommsError("name and body required")
    now = _utcnow()
    doc = {
        "name": name_s,
        "body": body_s,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    result = await templates_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return template_to_public(doc)


async def update_message_template(template_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        oid = ObjectId(template_id)
    except (InvalidId, TypeError) as exc:
        raise CccCommsError("Invalid template id") from exc
    updates: dict[str, Any] = {"updated_at": _utcnow()}
    if "name" in patch and patch["name"] is not None:
        updates["name"] = str(patch["name"]).strip()
    if "body" in patch and patch["body"] is not None:
        updates["body"] = str(patch["body"]).strip()
    if "enabled" in patch and patch["enabled"] is not None:
        updates["enabled"] = bool(patch["enabled"])
    await templates_collection.update_one({"_id": oid}, {"$set": updates})
    doc = await templates_collection.find_one({"_id": oid})
    if not doc:
        raise CccCommsError("Template not found")
    return template_to_public(doc)


async def send_incident_communication(
    user: dict,
    *,
    incident_id: str,
    body: str,
    subject: str = "",
    template_id: Optional[str] = None,
    recipient_user_id: Optional[str] = None,
    recipient_group: Optional[str] = None,
) -> dict[str, Any]:
    iid = (incident_id or "").strip()
    body_s = (body or "").strip()
    if not iid:
        raise CccCommsError("incident_id required")
    if not body_s and template_id:
        try:
            oid = ObjectId(template_id)
        except (InvalidId, TypeError) as exc:
            raise CccCommsError("Invalid template id") from exc
        tpl = await templates_collection.find_one({"_id": oid})
        if not tpl:
            raise CccCommsError("Template not found")
        body_s = str(tpl.get("body") or "").strip()
        if not subject:
            subject = str(tpl.get("name") or "")
    if not body_s:
        raise CccCommsError("body required")
    if not recipient_user_id and not recipient_group:
        raise CccCommsError("recipient_user_id or recipient_group required")

    now = _utcnow()
    # In-app: sent + delivered are knowable immediately (same software).
    doc = {
        "incident_id": iid,
        "template_id": template_id,
        "subject": (subject or "").strip(),
        "body": body_s,
        "recipient_user_id": (recipient_user_id or None),
        "recipient_group": (recipient_group or None),
        "status": "delivered",
        "sent_at": now,
        "delivered_at": now,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "sender": _actor(user),
        "channel": "ccc_internal",
        "external_delivery": False,
    }
    result = await comms_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Append timeline note on incident (best-effort).
    try:
        from app.services.ccc_incident_service import incidents_collection, timeline_entry

        entry = timeline_entry(
            entry_type="communication",
            actor=user,
            message=f"CCC message to {recipient_group or recipient_user_id}",
            detail={"communication_id": str(doc["_id"]), "channel": "ccc_internal"},
        )
        await incidents_collection.update_one(
            {"_id": ObjectId(iid)},
            {"$push": {"timeline": entry}, "$set": {"updated_at": now}},
        )
    except Exception:
        pass

    return comm_to_public(doc)


async def acknowledge_communication(comm_id: str, user: dict) -> dict[str, Any]:
    try:
        oid = ObjectId(comm_id)
    except (InvalidId, TypeError) as exc:
        raise CccCommsError("Invalid communication id") from exc
    doc = await comms_collection.find_one({"_id": oid})
    if not doc:
        raise CccCommsError("Communication not found")
    now = _utcnow()
    await comms_collection.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "acknowledged",
                "acknowledged_at": now,
                "acknowledged_by": _actor(user),
            }
        },
    )
    fresh = await comms_collection.find_one({"_id": oid})
    assert fresh is not None
    return comm_to_public(fresh)


async def list_incident_communications(incident_id: str) -> list[dict[str, Any]]:
    cursor = comms_collection.find({"incident_id": incident_id}).sort("sent_at", -1)
    return [comm_to_public(d) async for d in cursor]
