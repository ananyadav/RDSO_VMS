"""RDSO 18.6.15.4 — CCC Hot Screen for active high-priority situations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.ccc_incident_service import (
    CRITICAL_PRIORITY_MIN,
    incident_to_public,
    incidents_collection,
)


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _elapsed_seconds(start: Optional[datetime]) -> Optional[int]:
    if not start:
        return None
    return max(0, int((datetime.now(timezone.utc) - start).total_seconds()))


async def list_hot_screen_items(
    *,
    user: Optional[dict] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """
    Deterministic Hot Screen queue:
    priority DESC, then severity critical first, then oldest incident_time first
    (longest-running critical situations surface first within same priority).
    """
    limit_n = max(1, min(int(limit or 20), 50))
    query = {
        "status": {"$in": ["open", "assigned", "in_progress", "escalated"]},
        "$or": [
            {"priority": {"$gte": CRITICAL_PRIORITY_MIN}},
            {"severity": "critical"},
        ],
    }
    cursor = incidents_collection.find(query).sort(
        [("priority", -1), ("incident_time", 1)]
    ).limit(limit_n * 3)  # over-fetch then ACL-filter

    items: list[dict[str, Any]] = []
    async for doc in cursor:
        cams = list(doc.get("linked_camera_ids") or [])
        camera_id = cams[0] if cams else None
        # Camera ACL: drop linked video if unauthorized; still show incident metadata if Events user.
        live_href = None
        playback_href = None
        camera_authorized = False
        if camera_id and user:
            try:
                if is_admin(user):
                    camera_authorized = True
                else:
                    cam = await get_camera_by_ref(camera_id)
                    camera_authorized = bool(cam) and user_can_access_camera(
                        user, camera_id, cam
                    )
            except Exception:
                camera_authorized = False
            if camera_authorized:
                live_href = f"/live?camera={camera_id}"
                playback_href = f"/playback?camera={camera_id}"
            else:
                camera_id = None  # do not expose unauthorized camera identity for video

        started = _as_utc(doc.get("incident_time")) or _as_utc(doc.get("created_at"))
        pub = incident_to_public(doc)
        items.append(
            {
                "incident_id": pub["id"],
                "title": pub["title"],
                "location": pub["location"],
                "severity": pub["severity"],
                "priority": pub["priority"],
                "status": pub["status"],
                "assignee_group": pub.get("assignee_group"),
                "assignee_user_name": pub.get("assignee_user_name"),
                "elapsed_seconds": _elapsed_seconds(started),
                "incident_time": pub.get("incident_time"),
                "camera_id": camera_id if camera_authorized else None,
                "camera_authorized": camera_authorized,
                "live_href": live_href,
                "playback_href": playback_href,
                "workspace_href": "/ccc?tab=incidents",
                "via_vms_only": True,
                "direct_camera_rtsp": False,
            }
        )
        if len(items) >= limit_n:
            break

    return {
        "items": items,
        "total": len(items),
        "limit": limit_n,
        "order": "priority_desc,incident_time_asc",
        "critical_priority_min": CRITICAL_PRIORITY_MIN,
        "poll_seconds_suggested": 10,
        "rdso_18_6_15_4": True,
    }
