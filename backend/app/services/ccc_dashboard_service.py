"""RDSO 18.6.5 / 18.6.16.1 — CCC integrated dashboard metrics (real data only)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.ccc_incident_service import (
    CRITICAL_PRIORITY_MIN,
    incidents_collection,
)
from app.services.ccc_status import get_ccc_status_snapshot

logger = logging.getLogger(__name__)

DEFAULT_WIDGETS = [
    {"id": "cameras", "label": "Cameras", "enabled": True, "order": 0},
    {"id": "events", "label": "Events", "enabled": True, "order": 1},
    {"id": "incidents", "label": "Incidents", "enabled": True, "order": 2},
    {"id": "priority", "label": "Incident priority", "enabled": True, "order": 3},
    {"id": "health", "label": "VMS / recording health", "enabled": True, "order": 4},
    {"id": "storage", "label": "Storage", "enabled": True, "order": 5},
    {"id": "recent", "label": "Recent activity", "enabled": True, "order": 6},
    {"id": "compliance", "label": "SOP compliance", "enabled": True, "order": 7},
    {"id": "hot_screen", "label": "Hot Screen preview", "enabled": True, "order": 8},
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _camera_counts() -> dict[str, Any]:
    from app.core.database import camera_collection

    total = int(
        await camera_collection.count_documents(
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
        )
    )
    # Prefer explicit online flag when present; otherwise treat active as unknown/online-ish.
    online = int(
        await camera_collection.count_documents(
            {
                "$and": [
                    {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
                    {"$or": [{"online": True}, {"is_online": True}]},
                ]
            }
        )
    )
    offline_confirmed = int(
        await camera_collection.count_documents(
            {
                "$or": [
                    {"online": False},
                    {"is_online": False},
                    {"confirmed_offline": True},
                ]
            }
        )
    )
    # If no online flags are populated, report online as unknown (don't invent).
    flagged = int(
        await camera_collection.count_documents(
            {
                "$or": [
                    {"online": {"$exists": True}},
                    {"is_online": {"$exists": True}},
                ]
            }
        )
    )
    if flagged == 0:
        return {
            "total": total,
            "online": None,
            "offline": None,
            "online_status_known": False,
            "note": "Camera online flags not populated — total count only",
        }
    return {
        "total": total,
        "online": online,
        "offline": max(0, total - online) if total else offline_confirmed,
        "online_status_known": True,
    }


async def _event_counts() -> dict[str, Any]:
    from app.core.database import events_collection

    open_n = int(await events_collection.count_documents({"status": "open"}))
    critical = int(
        await events_collection.count_documents(
            {
                "$or": [
                    {"severity": "critical"},
                    {"priority": {"$gte": CRITICAL_PRIORITY_MIN}},
                ]
            }
        )
    )
    return {"open": open_n, "critical": critical}


async def _incident_counts() -> dict[str, Any]:
    open_n = int(
        await incidents_collection.count_documents(
            {"status": {"$in": ["open", "assigned", "in_progress"]}}
        )
    )
    escalated = int(await incidents_collection.count_documents({"status": "escalated"}))
    critical = int(
        await incidents_collection.count_documents(
            {
                "status": {"$nin": ["resolved", "closed"]},
                "$or": [
                    {"priority": {"$gte": CRITICAL_PRIORITY_MIN}},
                    {"severity": "critical"},
                ],
            }
        )
    )
    by_status: dict[str, int] = {}
    for st in ("open", "assigned", "in_progress", "escalated", "resolved", "closed"):
        by_status[st] = int(await incidents_collection.count_documents({"status": st}))
    by_priority: dict[str, int] = {}
    for p in range(1, 6):
        by_priority[str(p)] = int(
            await incidents_collection.count_documents(
                {"priority": p, "status": {"$nin": ["resolved", "closed"]}}
            )
        )
    return {
        "open_active": open_n,
        "escalated": escalated,
        "critical_open": critical,
        "by_status": by_status,
        "by_priority": by_priority,
    }


async def _recent_activity(limit: int = 8) -> dict[str, Any]:
    recent_incidents = []
    cursor = (
        incidents_collection.find({"status": {"$nin": ["closed"]}})
        .sort([("priority", -1), ("updated_at", -1)])
        .limit(limit)
    )
    async for doc in cursor:
        recent_incidents.append(
            {
                "id": str(doc["_id"]),
                "title": doc.get("title") or "",
                "status": doc.get("status"),
                "priority": doc.get("priority"),
                "location": doc.get("location") or "",
                "updated_at": doc.get("updated_at").isoformat()
                if isinstance(doc.get("updated_at"), datetime)
                else doc.get("updated_at"),
            }
        )

    recent_events = []
    try:
        from app.core.database import events_collection

        ev_cursor = events_collection.find({}).sort("occurred_at", -1).limit(limit)
        async for doc in ev_cursor:
            recent_events.append(
                {
                    "id": str(doc["_id"]),
                    "title": doc.get("title") or "",
                    "severity": doc.get("severity"),
                    "priority": doc.get("priority"),
                    "status": doc.get("status"),
                    "camera_id": doc.get("camera_id") or "",
                    "occurred_at": doc.get("occurred_at").isoformat()
                    if isinstance(doc.get("occurred_at"), datetime)
                    else doc.get("occurred_at"),
                }
            )
    except Exception as exc:
        logger.debug("[ccc-dashboard] recent events: %s", exc)

    return {"incidents": recent_incidents, "events": recent_events}


async def _storage_summary() -> dict[str, Any]:
    try:
        from app.services.storage_dashboard import get_storage_dashboard

        data = await get_storage_dashboard(summary_only=True)
        summary = data.get("summary") or {}
        disk = data.get("disk") or {}
        return {
            "ok": True,
            "free_percent": disk.get("free_percent") or summary.get("free_percent"),
            "used_percent": disk.get("used_percent") or summary.get("used_percent"),
            "status": disk.get("status") or summary.get("status") or data.get("status"),
            "path": disk.get("path") or summary.get("path"),
            "source": "storage_dashboard",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "source": "storage_dashboard"}


async def get_ccc_dashboard(*, user: Optional[dict] = None) -> dict[str, Any]:
    """Assemble real CCC dashboard metrics — never invent counts."""
    status = await get_ccc_status_snapshot(user=user)
    cameras = await _camera_counts()
    events = await _event_counts()
    incidents = await _incident_counts()
    recent = await _recent_activity()
    storage = await _storage_summary()

    compliance = {"pending": 0, "overdue": 0, "complete": 0}
    try:
        from app.services.ccc_compliance_service import summarize_compliance

        compliance = await summarize_compliance()
    except Exception as exc:
        compliance = {"error": str(exc)}

    hot_preview = []
    try:
        from app.services.ccc_hot_screen_service import list_hot_screen_items

        hot = await list_hot_screen_items(user=user, limit=3)
        hot_preview = hot.get("items") or []
    except Exception:
        hot_preview = []

    return {
        "rdso_18_6_5": True,
        "rdso_18_6_16_1": True,
        "generated_at": _utcnow().isoformat(),
        "fake_statistics": False,
        "cameras": cameras,
        "events": events,
        "incidents": incidents,
        "health": {
            "recording": status.get("recording") or {},
            "vms_ha": status.get("vms_ha") or {},
            "recording_ha": status.get("recording_ha") or {},
            "go2rtc": status.get("go2rtc") or {},
            "users_sessions": status.get("users_sessions") or {},
        },
        "storage": storage,
        "recent": recent,
        "compliance": compliance,
        "hot_screen_preview": hot_preview,
        "default_widgets": DEFAULT_WIDGETS,
        "poll_seconds_suggested": 15,
    }
