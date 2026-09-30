"""RDSO 18.6 — CCC incident persistence (linked to existing VMS events).

Does NOT replace the alarm engine. Incidents are an ops workflow layer on top
of persisted `events` documents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database
from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.priority_levels import (
    PriorityValidationError,
    normalize_priority,
    priority_from_severity,
    resolve_alarm_priority,
)

logger = logging.getLogger(__name__)

incidents_collection = database.get_collection("ccc_incidents")

INCIDENT_STATUSES = (
    "open",
    "assigned",
    "in_progress",
    "escalated",
    "resolved",
    "closed",
)
CRITICAL_PRIORITY_MIN = 4  # priority 4–5 = critical / must stay visible


class IncidentValidationError(ValueError):
    pass


class IncidentDuplicateError(ValueError):
    pass


class IncidentPermissionError(PermissionError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _oid(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def _actor_public(user: Optional[dict]) -> dict[str, str]:
    if not user:
        return {"id": "", "name": "", "role": ""}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("username") or ""),
        "role": str(user.get("role") or ""),
    }


def timeline_entry(
    *,
    entry_type: str,
    actor: Optional[dict],
    detail: Optional[dict] = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "at": _iso(_utcnow()),
        "type": entry_type,
        "actor": _actor_public(actor),
        "message": message or "",
        "detail": detail or {},
    }


def incident_to_public(doc: dict) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title") or "",
        "location": doc.get("location") or "",
        "status": doc.get("status") or "open",
        "incident_time": _iso(_parse_iso(doc.get("incident_time"))),
        "severity": doc.get("severity") or "info",
        "priority": int(doc.get("priority") or 3),
        "critical": int(doc.get("priority") or 0) >= CRITICAL_PRIORITY_MIN
        or str(doc.get("severity") or "").lower() == "critical",
        "assignee_user_id": doc.get("assignee_user_id") or None,
        "assignee_user_name": doc.get("assignee_user_name") or None,
        "assignee_group": doc.get("assignee_group") or None,
        "linked_event_ids": list(doc.get("linked_event_ids") or []),
        "linked_camera_ids": list(doc.get("linked_camera_ids") or []),
        "notes": list(doc.get("notes") or []),
        "timeline": list(doc.get("timeline") or []),
        "sop_workflow_id": doc.get("sop_workflow_id"),
        "sop_step_index": int(doc.get("sop_step_index") or 0),
        "escalation": doc.get("escalation") or {},
        "created_at": _iso(_parse_iso(doc.get("created_at"))),
        "updated_at": _iso(_parse_iso(doc.get("updated_at"))),
        "created_by": doc.get("created_by") or {},
        "updated_by": doc.get("updated_by") or {},
        "video": {
            "live_path_template": "/live?camera={camera_id}",
            "playback_path_template": "/playback?camera={camera_id}",
            "via_vms_only": True,
            "direct_camera_rtsp": False,
        },
        "rdso_18_6": True,
    }


async def ensure_incident_indexes() -> None:
    try:
        await incidents_collection.create_index("status", name="idx_ccc_incident_status")
        await incidents_collection.create_index("priority", name="idx_ccc_incident_priority")
        await incidents_collection.create_index(
            "incident_time", name="idx_ccc_incident_time"
        )
        await incidents_collection.create_index(
            "linked_event_ids", name="idx_ccc_incident_event_ids"
        )
        await incidents_collection.create_index(
            "linked_camera_ids", name="idx_ccc_incident_camera_ids"
        )
        await incidents_collection.create_index(
            "assignee_group", name="idx_ccc_incident_assignee_group"
        )
        # One open-ish incident per event id (multikey unique).
        await incidents_collection.create_index(
            "linked_event_ids",
            unique=True,
            name="idx_ccc_incident_event_unique",
            partialFilterExpression={"linked_event_ids.0": {"$exists": True}},
        )
    except Exception as exc:
        logger.warning("[ccc-incident] index setup: %s", exc)


def user_can_read_incidents(user: Optional[dict]) -> bool:
    from app.core.access_control import has_events_permission

    return has_events_permission(user)


def user_can_write_incidents(user: Optional[dict]) -> bool:
    """Multi-author updates: any Events-capable user (admins always)."""
    return user_can_read_incidents(user)


async def _assert_camera_acl(user: dict, camera_ids: list[str]) -> None:
    if is_admin(user):
        return
    for ref in camera_ids:
        if not ref:
            continue
        cam = await get_camera_by_ref(ref)
        if cam and not user_can_access_camera(user, ref, cam):
            raise IncidentPermissionError(f"No camera ACL for {ref}")


async def find_incident_by_event_id(event_id: str) -> Optional[dict]:
    eid = (event_id or "").strip()
    if not eid:
        return None
    return await incidents_collection.find_one({"linked_event_ids": eid})


async def create_incident(
    user: dict,
    *,
    title: str,
    location: str = "",
    status: str = "open",
    incident_time: Any = None,
    severity: str = "warning",
    priority: Any = None,
    assignee_user_id: Optional[str] = None,
    assignee_user_name: Optional[str] = None,
    assignee_group: Optional[str] = None,
    linked_event_ids: Optional[list[str]] = None,
    linked_camera_ids: Optional[list[str]] = None,
    notes: Optional[list] = None,
    sop_workflow_id: Optional[str] = None,
    source: str = "manual",
) -> dict:
    if not user_can_write_incidents(user):
        raise IncidentPermissionError("Incident write permission required")

    title_s = (title or "").strip()
    if not title_s:
        raise IncidentValidationError("title is required")

    status_s = (status or "open").strip().lower()
    if status_s not in INCIDENT_STATUSES:
        raise IncidentValidationError(f"invalid status: {status_s}")

    sev = (severity or "warning").strip().lower() or "warning"
    try:
        pri = resolve_alarm_priority(priority=priority, severity=sev)
    except PriorityValidationError as exc:
        raise IncidentValidationError(str(exc)) from exc

    event_ids = [str(x).strip() for x in (linked_event_ids or []) if str(x).strip()]
    camera_ids = [str(x).strip() for x in (linked_camera_ids or []) if str(x).strip()]

    for eid in event_ids:
        existing = await find_incident_by_event_id(eid)
        if existing:
            raise IncidentDuplicateError(
                f"Incident already exists for event {eid}: {existing['_id']}"
            )

    await _assert_camera_acl(user, camera_ids)

    now = _utcnow()
    when = _parse_iso(incident_time) or now
    actor = _actor_public(user)
    timeline = [
        timeline_entry(
            entry_type="created",
            actor=user,
            message=f"Incident created ({source})",
            detail={"source": source, "title": title_s},
        )
    ]
    if assignee_user_id or assignee_group:
        timeline.append(
            timeline_entry(
                entry_type="assignment",
                actor=user,
                message="Initial assignment",
                detail={
                    "assignee_user_id": assignee_user_id,
                    "assignee_group": assignee_group,
                },
            )
        )

    note_rows = []
    for n in notes or []:
        if isinstance(n, str) and n.strip():
            note_rows.append(
                {
                    "at": _iso(now),
                    "author": actor,
                    "text": n.strip(),
                }
            )
        elif isinstance(n, dict) and (n.get("text") or "").strip():
            note_rows.append(
                {
                    "at": n.get("at") or _iso(now),
                    "author": n.get("author") or actor,
                    "text": str(n.get("text")).strip(),
                }
            )

    doc = {
        "title": title_s,
        "location": (location or "").strip(),
        "status": status_s,
        "incident_time": when,
        "severity": sev,
        "priority": pri,
        "assignee_user_id": (assignee_user_id or None),
        "assignee_user_name": (assignee_user_name or None),
        "assignee_group": (assignee_group or None),
        "linked_event_ids": event_ids,
        "linked_camera_ids": camera_ids,
        "notes": note_rows,
        "timeline": timeline,
        "sop_workflow_id": sop_workflow_id,
        "sop_step_index": 0,
        "escalation": {"armed": True, "last_escalated_at": None, "count": 0},
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "updated_by": actor,
        "source": source,
    }
    try:
        result = await incidents_collection.insert_one(doc)
    except Exception as exc:
        # Unique index race → duplicate
        if "duplicate" in str(exc).lower() or getattr(exc, "code", None) == 11000:
            raise IncidentDuplicateError("Duplicate incident for linked event") from exc
        raise
    doc["_id"] = result.inserted_id
    return incident_to_public(doc)


async def promote_event_to_incident(user: dict, event_id: str, **overrides: Any) -> dict:
    """Create incident from an existing VMS event (no new alarm engine)."""
    from app.services.event_service import get_event

    event = await get_event(event_id, user)
    if not event:
        raise IncidentValidationError("Event not found or not accessible")

    existing = await find_incident_by_event_id(event["id"])
    if existing:
        raise IncidentDuplicateError(
            f"Incident already exists for event {event['id']}: {existing['_id']}"
        )

    cams = []
    if event.get("camera_id"):
        cams.append(event["camera_id"])
    if event.get("camera_uid") and event.get("camera_uid") not in cams:
        cams.append(event["camera_uid"])

    location = (
        overrides.get("location")
        or (event.get("metadata") or {}).get("location")
        or ""
    )
    return await create_incident(
        user,
        title=overrides.get("title") or event.get("title") or f"Event {event['id']}",
        location=str(location or ""),
        status=overrides.get("status") or "open",
        incident_time=overrides.get("incident_time") or event.get("occurred_at"),
        severity=overrides.get("severity") or event.get("severity") or "warning",
        priority=overrides.get("priority")
        if overrides.get("priority") is not None
        else event.get("priority"),
        assignee_user_id=overrides.get("assignee_user_id"),
        assignee_user_name=overrides.get("assignee_user_name"),
        assignee_group=overrides.get("assignee_group"),
        linked_event_ids=[event["id"]],
        linked_camera_ids=overrides.get("linked_camera_ids") or cams,
        notes=overrides.get("notes"),
        sop_workflow_id=overrides.get("sop_workflow_id"),
        source="promoted_event",
    )


async def get_incident(incident_id: str, user: dict) -> Optional[dict]:
    if not user_can_read_incidents(user):
        raise IncidentPermissionError("Incident read permission required")
    oid = _oid(incident_id)
    if not oid:
        return None
    doc = await incidents_collection.find_one({"_id": oid})
    if not doc:
        return None
    try:
        await _assert_camera_acl(user, list(doc.get("linked_camera_ids") or []))
    except IncidentPermissionError:
        return None
    return incident_to_public(doc)


async def list_incidents(
    user: dict,
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    priority_min: Optional[int] = None,
    critical_only: bool = False,
    assignee_group: Optional[str] = None,
    assignee_user_id: Optional[str] = None,
    location: Optional[str] = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    if not user_can_read_incidents(user):
        raise IncidentPermissionError("Incident read permission required")

    # Opportunistically apply due escalations (no separate redeploy/worker required).
    try:
        from app.services.ccc_incident_escalation import process_due_escalations

        await process_due_escalations(limit=20)
    except Exception as exc:
        logger.debug("[ccc-incident] escalation tick: %s", exc)

    limit_n = max(1, min(int(limit or 50), 200))
    offset_n = max(0, int(offset or 0))
    query: dict[str, Any] = {}
    if status:
        query["status"] = status.strip().lower()
    if severity:
        query["severity"] = severity.strip().lower()
    if assignee_group:
        query["assignee_group"] = assignee_group.strip()
    if assignee_user_id:
        query["assignee_user_id"] = assignee_user_id.strip()
    if location:
        query["location"] = {"$regex": location.strip(), "$options": "i"}
    if critical_only:
        query["$or"] = [
            {"priority": {"$gte": CRITICAL_PRIORITY_MIN}},
            {"severity": "critical"},
        ]
    elif priority_min is not None:
        query["priority"] = {"$gte": int(priority_min)}
    needle = (q or "").strip()
    if needle:
        query.setdefault("$and", []).append(
            {
                "$or": [
                    {"title": {"$regex": needle, "$options": "i"}},
                    {"location": {"$regex": needle, "$options": "i"}},
                ]
            }
        )

    total = int(await incidents_collection.count_documents(query))
    # Critical/high priority first, then newest.
    cursor = (
        incidents_collection.find(query)
        .sort([("priority", -1), ("incident_time", -1)])
        .skip(offset_n)
        .limit(limit_n)
    )
    items = []
    async for doc in cursor:
        try:
            await _assert_camera_acl(user, list(doc.get("linked_camera_ids") or []))
        except IncidentPermissionError:
            continue
        items.append(incident_to_public(doc))

    return {
        "items": items,
        "total": total,
        "limit": limit_n,
        "offset": offset_n,
        "returned": len(items),
        "sort": "priority_desc,incident_time_desc",
        "critical_priority_min": CRITICAL_PRIORITY_MIN,
        "rdso_18_6": True,
    }


async def update_incident(
    incident_id: str,
    user: dict,
    *,
    patch: dict[str, Any],
) -> dict:
    if not user_can_write_incidents(user):
        raise IncidentPermissionError("Incident write permission required")
    oid = _oid(incident_id)
    if not oid:
        raise IncidentValidationError("Invalid incident id")
    doc = await incidents_collection.find_one({"_id": oid})
    if not doc:
        raise IncidentValidationError("Incident not found")
    await _assert_camera_acl(user, list(doc.get("linked_camera_ids") or []))

    updates: dict[str, Any] = {}
    timeline_adds: list[dict] = []

    if "title" in patch and patch["title"] is not None:
        title_s = str(patch["title"]).strip()
        if not title_s:
            raise IncidentValidationError("title cannot be empty")
        if title_s != doc.get("title"):
            updates["title"] = title_s
            timeline_adds.append(
                timeline_entry(
                    entry_type="update",
                    actor=user,
                    message="Title changed",
                    detail={"title": title_s},
                )
            )

    if "location" in patch and patch["location"] is not None:
        loc = str(patch["location"]).strip()
        if loc != (doc.get("location") or ""):
            updates["location"] = loc
            timeline_adds.append(
                timeline_entry(
                    entry_type="update",
                    actor=user,
                    message="Location changed",
                    detail={"location": loc},
                )
            )

    if "status" in patch and patch["status"] is not None:
        st = str(patch["status"]).strip().lower()
        if st not in INCIDENT_STATUSES:
            raise IncidentValidationError(f"invalid status: {st}")
        if st != doc.get("status"):
            updates["status"] = st
            timeline_adds.append(
                timeline_entry(
                    entry_type="status_change",
                    actor=user,
                    message=f"Status {doc.get('status')} → {st}",
                    detail={"from": doc.get("status"), "to": st},
                )
            )

    if "severity" in patch and patch["severity"] is not None:
        updates["severity"] = str(patch["severity"]).strip().lower()

    if "priority" in patch and patch["priority"] is not None:
        try:
            updates["priority"] = normalize_priority(patch["priority"])
        except PriorityValidationError as exc:
            raise IncidentValidationError(str(exc)) from exc

    assign_changed = False
    if "assignee_user_id" in patch:
        updates["assignee_user_id"] = patch["assignee_user_id"] or None
        assign_changed = True
    if "assignee_user_name" in patch:
        updates["assignee_user_name"] = patch["assignee_user_name"] or None
        assign_changed = True
    if "assignee_group" in patch:
        updates["assignee_group"] = patch["assignee_group"] or None
        assign_changed = True
    if assign_changed:
        timeline_adds.append(
            timeline_entry(
                entry_type="assignment",
                actor=user,
                message="Assignment updated",
                detail={
                    "assignee_user_id": updates.get(
                        "assignee_user_id", doc.get("assignee_user_id")
                    ),
                    "assignee_group": updates.get(
                        "assignee_group", doc.get("assignee_group")
                    ),
                    "assignee_user_name": updates.get(
                        "assignee_user_name", doc.get("assignee_user_name")
                    ),
                },
            )
        )

    if "sop_workflow_id" in patch:
        updates["sop_workflow_id"] = patch["sop_workflow_id"]
    if "sop_step_index" in patch and patch["sop_step_index"] is not None:
        updates["sop_step_index"] = max(0, int(patch["sop_step_index"]))
        timeline_adds.append(
            timeline_entry(
                entry_type="sop_step",
                actor=user,
                message=f"SOP step → {updates['sop_step_index']}",
                detail={"sop_step_index": updates["sop_step_index"]},
            )
        )

    comment = patch.get("comment") or patch.get("note")
    if comment and str(comment).strip():
        note = {
            "at": _iso(_utcnow()),
            "author": _actor_public(user),
            "text": str(comment).strip(),
        }
        await incidents_collection.update_one(
            {"_id": oid}, {"$push": {"notes": note}}
        )
        timeline_adds.append(
            timeline_entry(
                entry_type="comment",
                actor=user,
                message=str(comment).strip()[:200],
            )
        )

    if "linked_camera_ids" in patch and isinstance(patch["linked_camera_ids"], list):
        cams = [str(x).strip() for x in patch["linked_camera_ids"] if str(x).strip()]
        await _assert_camera_acl(user, cams)
        updates["linked_camera_ids"] = cams

    updates["updated_at"] = _utcnow()
    updates["updated_by"] = _actor_public(user)

    ops: dict[str, Any] = {"$set": updates}
    if timeline_adds:
        ops["$push"] = {"timeline": {"$each": timeline_adds}}

    await incidents_collection.update_one({"_id": oid}, ops)
    fresh = await incidents_collection.find_one({"_id": oid})
    assert fresh is not None
    return incident_to_public(fresh)


async def list_event_log(
    user: dict,
    *,
    limit: int = 50,
    offset: int = 0,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    critical_only: bool = False,
    q: str = "",
) -> dict[str, Any]:
    """CCC Event Log: VMS events joined with incident status/assignee."""
    from app.services.event_service import list_events

    data = await list_events(
        user,
        severity=severity,
        status=None,
        limit=limit,
        offset=offset,
    )
    items = []
    for ev in data.get("items") or []:
        if critical_only:
            pri = int(ev.get("priority") or priority_from_severity(ev.get("severity")))
            if pri < CRITICAL_PRIORITY_MIN and str(ev.get("severity")).lower() != "critical":
                continue
        if status:
            # filter by linked incident status if requested
            pass
        inc = await find_incident_by_event_id(ev["id"])
        inc_pub = incident_to_public(inc) if inc else None
        if status and (not inc_pub or inc_pub.get("status") != status):
            continue
        if q:
            blob = f"{ev.get('title','')} {ev.get('camera_id','')} {ev.get('message','')}".lower()
            if q.lower() not in blob:
                continue
        items.append(
            {
                "event_id": ev["id"],
                "event_time": ev.get("occurred_at"),
                "camera_id": ev.get("camera_id") or "",
                "camera_uid": ev.get("camera_uid") or "",
                "location": (inc_pub or {}).get("location")
                or (ev.get("metadata") or {}).get("location")
                or "",
                "source_type": ev.get("source_type"),
                "severity": ev.get("severity"),
                "priority": ev.get("priority"),
                "title": ev.get("title"),
                "incident_id": (inc_pub or {}).get("id"),
                "incident_status": (inc_pub or {}).get("status"),
                "assignee_user_id": (inc_pub or {}).get("assignee_user_id"),
                "assignee_group": (inc_pub or {}).get("assignee_group"),
                "assignee_user_name": (inc_pub or {}).get("assignee_user_name"),
                "critical": bool((inc_pub or {}).get("critical"))
                or int(ev.get("priority") or 0) >= CRITICAL_PRIORITY_MIN
                or str(ev.get("severity") or "").lower() == "critical",
                "live_href": f"/live?camera={ev.get('camera_id') or ''}"
                if ev.get("camera_id")
                else None,
                "playback_href": f"/playback?camera={ev.get('camera_id') or ''}"
                if ev.get("camera_id")
                else None,
            }
        )

    # Critical rows first so they are never buried under lower-priority noise.
    items.sort(
        key=lambda r: (
            0 if r.get("critical") else 1,
            -(1 if r.get("event_time") else 0),
            str(r.get("event_time") or ""),
        )
    )
    # Newest within each critical/non-critical band
    crit = [r for r in items if r.get("critical")]
    rest = [r for r in items if not r.get("critical")]
    crit.sort(key=lambda r: str(r.get("event_time") or ""), reverse=True)
    rest.sort(key=lambda r: str(r.get("event_time") or ""), reverse=True)
    items = crit + rest

    return {
        "items": items,
        "total": data.get("total", len(items)),
        "limit": data.get("limit", limit),
        "offset": data.get("offset", offset),
        "returned": len(items),
        "rdso_18_6": True,
    }
