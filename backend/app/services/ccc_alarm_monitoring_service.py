"""RDSO 18.6.22.9 — CCC alarm monitoring state + zone/camera response.

Uses existing VMS events and camera location hierarchy. Does not invent
geographic distance. Video/snapshots only via VMS → /media/go2rtc.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.access_control import has_live_view
from app.core.database import camera_collection, database, events_collection
from app.services.camera_access import is_admin, user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.client_media_routing import build_client_media_routing, frame_jpeg_path
from app.services.event_service import event_to_public
from app.services.go2rtc_service import stream_name
from app.services.priority_levels import sort_alarms_by_priority

# Centralized CCC monitoring modes (idle = normal monitoring).
STATE_MONITORING = "MONITORING"
STATE_ALARM = "ALARM"

_zone_map = database.get_collection("ccc_zone_camera_map")
_SETTINGS_ID = "ccc_zone_camera_map"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


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
    return max(0, int((_utcnow() - start).total_seconds()))


def _meta(doc: dict) -> dict:
    md = doc.get("metadata")
    return md if isinstance(md, dict) else {}


def is_alarm_recovered(doc: dict) -> bool:
    """True when alarm must leave active CCC monitoring."""
    if bool(doc.get("acknowledged")):
        return True
    if str(doc.get("status") or "").lower() == "acknowledged":
        return True
    md = _meta(doc)
    if md.get("monitoring_recovered") or md.get("signal_restored"):
        return True
    if md.get("recovered_at") or md.get("monitoring_recovered_at"):
        return True
    if md.get("display_reset"):
        return True
    return False


def _location_path_from_cam(cam: Optional[dict]) -> str:
    if not cam:
        return ""
    path = str(cam.get("location_path") or "").strip()
    if path:
        return path
    parts = [
        str(cam.get("site") or "").strip(),
        str(cam.get("building") or "").strip(),
        str(cam.get("floor") or cam.get("floor_group") or "").strip(),
        str(cam.get("area") or "").strip(),
    ]
    return " / ".join(p for p in parts if p)


def resolve_affected_zone(
    event_doc: dict,
    camera_doc: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Derive affected zone without inventing geographic distance.

    Order: explicit zone association → location hierarchy → honest unknown.
    Coordinates only if already present on the document (none exist today).
    """
    md = _meta(event_doc)
    explicit = (
        md.get("zone")
        or md.get("affected_zone")
        or md.get("location_zone")
        or md.get("zone_id")
    )
    if explicit is not None and str(explicit).strip():
        zone = str(explicit).strip()
        return {
            "zone": zone,
            "zone_key": zone.lower(),
            "source": "explicit",
            "location_path": str(md.get("location") or md.get("location_path") or zone),
            "unknown": False,
            "geo_distance_used": False,
        }

    loc = str(md.get("location") or md.get("location_path") or "").strip()
    if loc:
        return {
            "zone": loc,
            "zone_key": loc.lower(),
            "source": "event_location",
            "location_path": loc,
            "unknown": False,
            "geo_distance_used": False,
        }

    cam_path = _location_path_from_cam(camera_doc)
    if cam_path:
        return {
            "zone": cam_path,
            "zone_key": cam_path.lower(),
            "source": "camera_location_hierarchy",
            "location_path": cam_path,
            "site": (camera_doc or {}).get("site"),
            "building": (camera_doc or {}).get("building"),
            "floor": (camera_doc or {}).get("floor") or (camera_doc or {}).get("floor_group"),
            "area": (camera_doc or {}).get("area"),
            "camera_group": (camera_doc or {}).get("camera_group"),
            "unknown": False,
            "geo_distance_used": False,
        }

    # No genuine coordinates in this codebase — never invent distance.
    return {
        "zone": None,
        "zone_key": None,
        "source": "unknown",
        "location_path": None,
        "unknown": True,
        "geo_distance_used": False,
        "note": "No explicit zone or location hierarchy available for this alarm",
    }


async def get_zone_camera_map() -> dict[str, Any]:
    doc = await _zone_map.find_one({"_id": _SETTINGS_ID})
    mappings = []
    if doc and isinstance(doc.get("mappings"), list):
        for row in doc["mappings"]:
            if not isinstance(row, dict):
                continue
            zk = str(row.get("zone_key") or row.get("zone") or "").strip().lower()
            cam = str(row.get("preferred_camera_id") or "").strip()
            if zk and cam:
                mappings.append(
                    {
                        "zone_key": zk,
                        "zone": str(row.get("zone") or zk),
                        "preferred_camera_id": cam,
                        "prefer_ptz": bool(row.get("prefer_ptz", True)),
                    }
                )
    return {
        "mappings": mappings,
        "updated_at": doc.get("updated_at").isoformat()
        if doc and isinstance(doc.get("updated_at"), datetime)
        else (doc or {}).get("updated_at"),
        "rdso_18_6_22_9": True,
        "note": "Admin zone → preferred camera/PTZ. Matching is prefix/exact on zone_key.",
    }


async def save_zone_camera_map(mappings: list[dict[str, Any]]) -> dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for row in mappings or []:
        if not isinstance(row, dict):
            continue
        zone = str(row.get("zone") or row.get("zone_key") or "").strip()
        zk = str(row.get("zone_key") or zone).strip().lower()
        cam = str(row.get("preferred_camera_id") or "").strip()
        if not zk or not cam:
            continue
        try:
            ObjectId(cam)
        except (InvalidId, TypeError) as exc:
            raise ValueError(f"preferred_camera_id must be a Mongo id: {cam}") from exc
        cleaned.append(
            {
                "zone": zone or zk,
                "zone_key": zk,
                "preferred_camera_id": cam,
                "prefer_ptz": bool(row.get("prefer_ptz", True)),
            }
        )
    now = _utcnow()
    await _zone_map.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": {"_id": _SETTINGS_ID, "mappings": cleaned, "updated_at": now}},
        upsert=True,
    )
    return await get_zone_camera_map()


def _zone_map_match(
    zone_info: dict[str, Any], mappings: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    zk = str(zone_info.get("zone_key") or "").strip().lower()
    if not zk:
        return None
    # Exact first, then longest prefix match on either side.
    exact = next((m for m in mappings if m["zone_key"] == zk), None)
    if exact:
        return exact
    best = None
    best_len = 0
    for m in mappings:
        mk = m["zone_key"]
        if zk.startswith(mk) or mk.startswith(zk):
            if len(mk) > best_len:
                best = m
                best_len = len(mk)
    return best


def _is_ptz(cam: dict) -> bool:
    return bool(cam.get("ptz") or cam.get("is_ptz"))


def _same_location_family(a: dict, b: dict) -> bool:
    """Related cameras via hierarchy — not geographic distance."""
    ag = str(a.get("camera_group") or "").strip()
    bg = str(b.get("camera_group") or "").strip()
    if ag and bg and ag == bg:
        return True
    ap = _location_path_from_cam(a)
    bp = _location_path_from_cam(b)
    if ap and bp and (ap == bp or ap.startswith(bp) or bp.startswith(ap)):
        return True
    # Same site+building+floor when path missing
    keys = ("site", "building")
    if all(str(a.get(k) or "").strip() and str(a.get(k)) == str(b.get(k)) for k in keys):
        af = str(a.get("floor") or a.get("floor_group") or "").strip()
        bf = str(b.get("floor") or b.get("floor_group") or "").strip()
        if af and bf and af == bf:
            return True
    return False


async def _find_related_cameras(seed: dict) -> list[dict]:
    """Cameras sharing hierarchy with seed (capped)."""
    query: dict[str, Any] = {
        "$or": [{"is_active": True}, {"is_active": {"$exists": False}}],
    }
    cg = str(seed.get("camera_group") or "").strip()
    if cg:
        query = {"$and": [query, {"camera_group": cg}]}
    else:
        site = str(seed.get("site") or "").strip()
        building = str(seed.get("building") or "").strip()
        floor = str(seed.get("floor") or seed.get("floor_group") or "").strip()
        parts = []
        if site:
            parts.append({"site": site})
        if building:
            parts.append({"building": building})
        if floor:
            parts.append({"$or": [{"floor": floor}, {"floor_group": floor}]})
        if parts:
            query = {"$and": [query, *parts]}
        else:
            # Cannot broaden to entire fleet — return empty related set.
            return []
    out: list[dict] = []
    cursor = camera_collection.find(query).limit(40)
    async for doc in cursor:
        out.append(doc)
    return out


async def resolve_response_camera(
    *,
    event_doc: dict,
    alarm_camera: Optional[dict],
    zone_info: dict[str, Any],
    zone_map: Optional[dict[str, Any]] = None,
    user: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Selection order:
    1) Admin zone → preferred camera/PTZ mapping
    2) Configured alarm camera (event.camera_id)
    3) Related PTZ in same location hierarchy (when determinable)
    4) Related / alarm fixed camera
    """
    zmap = zone_map or await get_zone_camera_map()
    mappings = list(zmap.get("mappings") or [])
    selection_reason = "none"
    chosen: Optional[dict] = None

    match = _zone_map_match(zone_info, mappings)
    if match:
        preferred = await get_camera_by_ref(match["preferred_camera_id"])
        if preferred and _camera_allowed(user, preferred):
            chosen = preferred
            selection_reason = "zone_preferred_mapping"

    if chosen is None and alarm_camera and _camera_allowed(user, alarm_camera):
        # Prefer mapped PTZ sibling when alarm cam is fixed and prefer_ptz.
        prefer_ptz = True if not match else bool(match.get("prefer_ptz", True))
        if prefer_ptz and not _is_ptz(alarm_camera):
            related = await _find_related_cameras(alarm_camera)
            ptz = next(
                (
                    c
                    for c in related
                    if _is_ptz(c)
                    and _same_location_family(alarm_camera, c)
                    and _camera_allowed(user, c)
                ),
                None,
            )
            if ptz:
                chosen = ptz
                selection_reason = "related_ptz"
            else:
                chosen = alarm_camera
                selection_reason = "configured_alarm_camera"
        else:
            chosen = alarm_camera
            selection_reason = (
                "configured_alarm_camera_ptz"
                if _is_ptz(alarm_camera)
                else "configured_alarm_camera"
            )

    if chosen is None and alarm_camera is None and not zone_info.get("unknown"):
        # Zone known but no alarm camera — try mapped already handled; try hierarchy search via zone path.
        path = str(zone_info.get("location_path") or "").strip()
        if path:
            cursor = camera_collection.find(
                {
                    "$or": [
                        {"location_path": path},
                        {"location_path": {"$regex": f"^{path}", "$options": "i"}},
                    ]
                }
            ).limit(20)
            cands = [d async for d in cursor]
            ptz = next((c for c in cands if _is_ptz(c) and _camera_allowed(user, c)), None)
            fixed = next((c for c in cands if not _is_ptz(c) and _camera_allowed(user, c)), None)
            if ptz:
                chosen = ptz
                selection_reason = "zone_related_ptz"
            elif fixed:
                chosen = fixed
                selection_reason = "zone_related_fixed"

    if chosen is None:
        return {
            "camera_id": None,
            "camera_name": None,
            "camera_uid": None,
            "ptz": False,
            "selection_reason": "unresolved",
            "camera_authorized": False,
            "live_available": False,
            "snapshot": None,
            "media": None,
            "via_vms_only": True,
            "direct_camera_rtsp": False,
        }

    cid = str(chosen.get("_id") or "")
    uid = str(chosen.get("camera_uid") or "").strip()
    authorized = _camera_allowed(user, chosen)
    live_ok = bool(authorized and user and (is_admin(user) or has_live_view(user)))
    media = None
    snapshot = None
    if live_ok:
        routing = build_client_media_routing(chosen)
        media = {
            "worker_id": routing["live"]["worker_id"],
            "ws_path": routing["live"]["ws_path"],
            "stream_src_hint": routing["live"]["stream_src_hint"],
            "relative_path": True,
        }
        snap_src = stream_name(uid or cid, "sub") if (uid or cid) else None
        if snap_src:
            wid = routing["live"]["worker_id"]
            snapshot = {
                "on_demand": True,
                "via_vms_go2rtc": True,
                "frame_jpeg_path": frame_jpeg_path(wid, snap_src),
                "src": snap_src,
                "worker_id": wid,
                "direct_camera": False,
            }

    return {
        "camera_id": cid if authorized else None,
        "camera_name": (
            chosen.get("display_name") or chosen.get("name") or cid
        )
        if authorized
        else None,
        "camera_uid": uid if authorized else None,
        "ptz": _is_ptz(chosen) if authorized else False,
        "selection_reason": selection_reason if authorized else "acl_denied",
        "camera_authorized": authorized,
        "live_available": live_ok,
        "snapshot": snapshot if live_ok else None,
        "media": media if live_ok else None,
        "live_href": f"/live?camera={cid}" if live_ok else None,
        "via_vms_only": True,
        "direct_camera_rtsp": False,
    }


def _camera_allowed(user: Optional[dict], cam: dict) -> bool:
    if not user:
        return True
    if is_admin(user):
        return True
    cid = str(cam.get("_id") or cam.get("id") or "")
    return user_can_access_camera(user, cid, cam)


def _active_alarm_query() -> dict[str, Any]:
    return {
        "status": "open",
        "acknowledged": {"$ne": True},
        "$and": [
            {"metadata.monitoring_recovered": {"$ne": True}},
            {"metadata.signal_restored": {"$ne": True}},
            {"metadata.display_reset": {"$ne": True}},
            {
                "$or": [
                    {"metadata.recovered_at": {"$exists": False}},
                    {"metadata.recovered_at": None},
                    {"metadata.recovered_at": ""},
                ]
            },
        ],
    }


async def list_alarm_monitoring(
    *,
    user: Optional[dict] = None,
    limit: int = 20,
) -> dict[str, Any]:
    limit_n = max(1, min(int(limit or 20), 50))
    zone_map = await get_zone_camera_map()
    query = _active_alarm_query()
    # Over-fetch then priority-sort + ACL (events without camera ACL for external_sensor OK).
    cursor = events_collection.find(query).sort([("occurred_at", -1)]).limit(limit_n * 5)
    raw: list[dict] = []
    async for doc in cursor:
        if is_alarm_recovered(doc):
            continue
        raw.append(doc)

    # Build public items with zone + camera response, then stable priority order.
    items: list[dict[str, Any]] = []
    for doc in raw:
        cid = str(doc.get("camera_id") or "").strip()
        cam = await get_camera_by_ref(cid) if cid else None
        # Event ACL: skip if camera present and unauthorized (external_sensor w/o cam OK).
        if cid and user and not is_admin(user):
            if not cam or not user_can_access_camera(user, cid, cam):
                continue
        zone = resolve_affected_zone(doc, cam)
        response = await resolve_response_camera(
            event_doc=doc,
            alarm_camera=cam,
            zone_info=zone,
            zone_map=zone_map,
            user=user,
        )
        pub = event_to_public(doc)
        started = _as_utc(doc.get("occurred_at"))
        items.append(
            {
                "event_id": pub["id"],
                "title": pub.get("title") or "",
                "message": pub.get("message") or "",
                "source_type": pub.get("source_type"),
                "type": pub.get("source_type"),
                "severity": pub.get("severity"),
                "priority": pub.get("priority"),
                "status": pub.get("status"),
                "state": "ALARM",
                "occurred_at": pub.get("occurred_at"),
                "elapsed_seconds": _elapsed_seconds(started),
                "affected_zone": zone,
                "camera_response": response,
                "camera_id": response.get("camera_id"),
                "camera_authorized": response.get("camera_authorized"),
                "ptz": response.get("ptz"),
                "selection_reason": response.get("selection_reason"),
                "snapshot": response.get("snapshot"),
                "media": response.get("media"),
                "live_href": response.get("live_href"),
                "via_vms_only": True,
                "direct_camera_rtsp": False,
                "hot_screen_href": "/ccc?tab=hot-screen",
            }
        )

    ordered = sort_alarms_by_priority(items)[:limit_n]
    active = len(ordered) > 0
    monitoring_state = STATE_ALARM if active else STATE_MONITORING
    return {
        "monitoring_state": monitoring_state,
        "monitoring_label": "ALARM" if active else "NORMAL / MONITORING",
        "is_alarm": active,
        "active_count": len(ordered),
        "items": ordered,
        "total": len(ordered),
        "limit": limit_n,
        "order": "priority_desc,severity_desc,occurred_at_desc",
        "poll_seconds_suggested": 5,
        "video_path": "CCC → VMS → /media/go2rtc",
        "direct_camera_rtsp_forbidden": True,
        "geo_distance_invented": False,
        "rdso_18_6_22_9": True,
    }


async def recover_alarm_monitoring(
    event_id: str,
    user: dict,
    *,
    reason: str = "operator_reset",
) -> Optional[dict]:
    """Mark alarm recovered/reset for CCC monitoring (does not require disconnect)."""
    eid = (event_id or "").strip()
    try:
        oid = ObjectId(eid)
    except (InvalidId, TypeError):
        return None
    doc = await events_collection.find_one({"_id": oid})
    if not doc:
        return None
    cid = str(doc.get("camera_id") or "").strip()
    if cid and not is_admin(user):
        cam = await get_camera_by_ref(cid)
        if not cam or not user_can_access_camera(user, cid, cam):
            return None
    now = _utcnow()
    actor_id = str(user.get("_id") or user.get("id") or "")
    await events_collection.update_one(
        {"_id": oid},
        {
            "$set": {
                "metadata.monitoring_recovered": True,
                "metadata.monitoring_recovered_at": _iso(now),
                "metadata.recovered_at": _iso(now),
                "metadata.monitoring_recover_reason": str(reason or "operator_reset")[:120],
                "metadata.monitoring_recovered_by": actor_id,
            }
        },
    )
    updated = await events_collection.find_one({"_id": oid})
    return event_to_public(updated) if updated else None
