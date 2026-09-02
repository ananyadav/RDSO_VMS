import logging
import asyncio
import re
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from bson.errors import InvalidId

from app.core.database import (
    camera_collection,
    get_all_cameras_from_db,
    create_camera,
    upsert_camera_by_ip,
    update_camera as db_update_camera,
)
from app.services.camera_form import (
    duplicate_conflict_response,
    find_duplicate_camera,
    normalize_ip_address,
    prepare_camera_fields,
    public_camera_response,
    validate_camera_payload,
)
from app.services.recording_schedule_store import register_camera_for_recording
from app.services.camera_sync import (
    apply_bulk_camera_side_effects,
    finalize_camera_fields,
    schedule_camera_side_effects,
    stream_config_changed,
)
from app.services.camera_bulk_import import bulk_import_cameras
from app.services.camera_discovery import (
    discover_cameras_full,
    normalize_discovery_ip,
    subnets_from_camera_ips,
)
from app.core.auth_context import get_effective_user
from app.services.camera_access import (
    active_camera_filter,
    build_access_filter,
    camera_access_public,
    is_admin,
    merge_query,
    user_can_access_camera,
)
from app.services.camera_locations import (
    build_floor_group_meta,
    build_groups_hierarchy,
    default_location_for_camera,
    infer_camera_group_from_name,
    legacy_camera_group_aliases,
    location_fields_for_group,
)
from app.services.camera_identity import (
    camera_display_name,
    has_unmapped_recordings,
    legacy_playback_camera_item,
    make_camera_uid,
)
from app.core.auth_context import get_effective_user
logger = logging.getLogger(__name__)

_CAM_NUM_RE = re.compile(r"^Cam(\d+)$", re.IGNORECASE)


def _display_label(cam: dict) -> str:
    """Live-view label: display_name, then name, then ip_address."""
    ip = (cam.get("ip_address") or "").strip()
    for key in ("display_name", "name", "ip_address"):
        val = (cam.get(key) or "").strip()
        if val:
            return val
    from app.services.camera_uid import ip_from_camera_uid

    derived = ip_from_camera_uid(cam.get("camera_uid") or make_camera_uid(ip) or "")
    if derived:
        return derived
    return str(cam.get("_id", ""))


def _camera_list_item(cam: dict, *, admin: bool = False) -> dict:
    is_active = cam.get("is_active")
    if is_active is None:
        is_active = True
    ip = (cam.get("ip_address") or "").strip()
    uid = cam.get("camera_uid") or make_camera_uid(ip) or ""
    item = {
        "id": str(cam["_id"]) if isinstance(cam.get("_id"), ObjectId) else cam.get("_id"),
        "cameraUid": uid,
        "name": cam.get("name", ""),
        "displayName": _display_label(cam),
        "online": False,
        "site": cam.get("site", ""),
        "building": cam.get("building", ""),
        "floor_group": cam.get("floor_group", ""),
        "floor": cam.get("floor", ""),
        "camera_group": cam.get("camera_group", ""),
        "location_path": cam.get("location_path", ""),
        "ip_address": ip,
        "is_active": bool(is_active),
        "ptz": bool(cam.get("ptz")),
        "activity": bool(cam.get("activity")),
    }
    wid = cam.get("worker_id")
    try:
        from app.services.go2rtc_workers import normalize_worker_id

        item["workerId"] = normalize_worker_id(wid) or 1
    except Exception:
        item["workerId"] = 1 if wid in (None, "") else wid
    if admin:
        item["ip_address"] = ip
        item["camera_uid"] = uid
    return item


def _camera_management_item(cam: dict) -> dict:
    item = _camera_list_item(cam, admin=True)
    protocol = (cam.get("protocol") or "HIKVISION").upper()
    item.update({
        "_id": str(cam["_id"]) if isinstance(cam.get("_id"), ObjectId) else cam.get("_id"),
        "ip_address": cam.get("ip_address", ""),
        "port": cam.get("port", 554),
        "model": cam.get("model", ""),
        "username": cam.get("username", "admin"),
        "password": cam.get("password", ""),
        "protocol": protocol,
        "area": cam.get("area", ""),
        "site": cam.get("site", ""),
        "main_channel": cam.get("main_channel", "101"),
        "sub_channel": cam.get("sub_channel", "102"),
        "recording_channel": cam.get("recording_channel", ""),
        "main_rtsp_url": cam.get("main_rtsp_url", ""),
        "sub_rtsp_url": cam.get("sub_rtsp_url", ""),
        "rtsp_url_source": cam.get("rtsp_url_source", ""),
        "worker_id": cam.get("worker_id", ""),
        "live_provider": cam.get("live_provider", "go2rtc"),
        "ptz": bool(cam.get("ptz")),
        "http_port": cam.get("http_port", 80),
        "ptz_channel": cam.get("ptz_channel", 1),
        "online": False,
        "status": "Disabled" if cam.get("is_active") is False else "Active",
    })
    return item


def _parse_filters(request) -> Dict[str, Any]:
    q = request.rel_url.query
    include_inactive = (q.get("includeInactive") or "").lower() in ("1", "true", "yes")
    filters: Dict[str, Any] = {}
    if q.get("camera_group"):
        filters["camera_group"] = q.get("camera_group").strip()
    if q.get("building"):
        filters["building"] = q.get("building").strip()
    if q.get("floor"):
        filters["floor"] = q.get("floor").strip()
    if q.get("site"):
        filters["site"] = q.get("site").strip()
    protocol = (q.get("protocol") or "").strip()
    if protocol:
        filters["protocol"] = protocol.upper()
    active_only = (q.get("activeOnly") or "").lower() in ("1", "true", "yes")
    if active_only:
        filters["active_only"] = True
    search = (q.get("search") or "").strip()
    if search:
        filters["search"] = search
    online_raw = (q.get("online") or "").strip().lower()
    if online_raw in ("1", "true", "yes", "online"):
        filters["online"] = True
    elif online_raw in ("0", "false", "no", "offline"):
        filters["online"] = False
    filters["include_inactive"] = include_inactive
    limit_raw = (q.get("limit") or "").strip()
    if limit_raw.isdigit():
        filters["limit"] = min(max(1, int(limit_raw)), 500)
    offset_raw = (q.get("offset") or "").strip()
    if offset_raw.isdigit():
        filters["offset"] = max(0, int(offset_raw))
    ids_raw = (q.get("ids") or "").strip()
    if ids_raw:
        filters["ids"] = [part.strip() for part in ids_raw.split(",") if part.strip()]
    return filters


def _site_scope_or_clauses(
    site: str,
    *,
    floor_meta: Dict[str, Dict[str, str]] | None = None,
) -> List[Dict[str, Any]]:
    """Match all cameras for a site (Load all cameras) — same rules as location hierarchy."""
    from app.services.location_store import slugify

    site_name = (site or "").strip()
    if not site_name:
        return []

    or_clauses: List[Dict[str, Any]] = [
        {"site": {"$regex": f"^{re.escape(site_name)}$", "$options": "i"}},
    ]
    site_slug = slugify(site_name)
    if site_slug:
        or_clauses.append(
            {"camera_group": {"$regex": f"^{re.escape(site_slug)}_", "$options": "i"}}
        )

    if floor_meta:
        groups: set[str] = set()
        for group, meta in floor_meta.items():
            if (meta.get("site") or "").strip().lower() != site_name.lower():
                continue
            groups.add(group)
            for alias in legacy_camera_group_aliases(group, site=site_name):
                groups.add(alias)
        for group in groups:
            or_clauses.append({"camera_group": group})
            meta = floor_meta.get(group) or {}
            building = (meta.get("building") or "").strip()
            floor = (meta.get("floor") or meta.get("floor_group") or "").strip()
            if building and floor:
                or_clauses.append(
                    {
                        "building": {"$regex": f"^{re.escape(building)}$", "$options": "i"},
                        "$or": [{"floor": floor}, {"floor_group": floor}],
                    }
                )

    return or_clauses


def _location_filters(
    filters: Dict[str, Any],
    *,
    floor_meta: Dict[str, Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    q: Dict[str, Any] = {}
    and_clauses: List[Dict[str, Any]] = []
    group_filter = (filters.get("camera_group") or "").strip()
    if group_filter:
        meta = location_fields_for_group(group_filter, floor_meta=floor_meta)
        building = (filters.get("building") or meta.get("building") or "").strip()
        floor = (
            filters.get("floor")
            or meta.get("floor")
            or meta.get("floor_group")
            or ""
        ).strip()
        site = (filters.get("site") or meta.get("site") or "").strip()
        or_clauses: List[Dict[str, Any]] = [
            {"camera_group": alias}
            for alias in legacy_camera_group_aliases(group_filter, site=site or None)
        ]
        or_clauses.append({"camera_group": group_filter})
        if meta.get("building") and meta.get("floor"):
            loc_match: Dict[str, Any] = {
                "building": {"$regex": f"^{re.escape(meta['building'])}$", "$options": "i"},
                "$or": [
                    {"floor": meta["floor"]},
                    {"floor_group": meta["floor"]},
                ],
            }
            if meta.get("site"):
                loc_match["site"] = {"$regex": f"^{re.escape(meta['site'])}$", "$options": "i"}
            or_clauses.append(loc_match)
        if building and floor:
            or_clauses.extend(
                [
                    {"building": building, "floor": floor},
                    {"building": building, "floor_group": floor},
                    {
                        "building": {"$regex": f"^{re.escape(building)}$", "$options": "i"},
                        "floor": {"$regex": f"^{re.escape(floor)}$", "$options": "i"},
                    },
                    {
                        "building": {"$regex": f"^{re.escape(building)}$", "$options": "i"},
                        "floor_group": {"$regex": f"^{re.escape(floor)}$", "$options": "i"},
                    },
                ]
            )
            if site:
                or_clauses.extend(
                    [
                        {
                            "site": {"$regex": f"^{re.escape(site)}$", "$options": "i"},
                            "building": {"$regex": f"^{re.escape(building)}$", "$options": "i"},
                            "floor": {"$regex": f"^{re.escape(floor)}$", "$options": "i"},
                        },
                        {
                            "site": {"$regex": f"^{re.escape(site)}$", "$options": "i"},
                            "building": {"$regex": f"^{re.escape(building)}$", "$options": "i"},
                            "floor_group": {"$regex": f"^{re.escape(floor)}$", "$options": "i"},
                        },
                    ]
                )
        and_clauses.append({"$or": or_clauses})
    if filters.get("building") and not group_filter:
        building = (filters.get("building") or "").strip()
        if building:
            q["building"] = {"$regex": f"^{re.escape(building)}$", "$options": "i"}
        site = (filters.get("site") or "").strip()
        if site:
            q["site"] = {"$regex": f"^{re.escape(site)}$", "$options": "i"}
    if filters.get("floor") and not group_filter:
        q["floor"] = filters["floor"]
    site_filter = (filters.get("site") or "").strip()
    if site_filter and not group_filter and not filters.get("building"):
        site_clauses = _site_scope_or_clauses(site_filter, floor_meta=floor_meta)
        if site_clauses:
            and_clauses.append({"$or": site_clauses})
    if filters.get("search"):
        s = filters["search"]
        and_clauses.append(
            {
                "$or": [
                    {"name": {"$regex": s, "$options": "i"}},
                    {"ip_address": {"$regex": s, "$options": "i"}},
                ]
            }
        )
    if filters.get("protocol"):
        q["protocol"] = filters["protocol"]
    if filters.get("ptz") is True:
        q["ptz"] = True
    if and_clauses:
        q["$and"] = and_clauses
    return q


_LIVE_CAMERA_PROJECTION = {
    "_id": 1,
    "name": 1,
    "ip_address": 1,
    "camera_uid": 1,
    "site": 1,
    "building": 1,
    "floor": 1,
    "floor_group": 1,
    "camera_group": 1,
    "location_path": 1,
    "is_active": 1,
    "ptz": 1,
    "activity": 1,
    "worker_id": 1,
}


async def query_cameras(
    user: Optional[dict],
    filters: Optional[Dict[str, Any]] = None,
    *,
    management: bool = False,
    lean: bool = False,
) -> List[dict]:
    """Query cameras with location filters, active filter, and access control.

    lean=True (Live View): skip location-catalog round-trip and use a thin Mongo
    projection so large site/building scopes return quickly.
    """
    import time as _time

    filters = filters or {}
    include_inactive = bool(filters.get("include_inactive")) and is_admin(user)
    active_only = bool(filters.get("active_only"))

    t_loc0 = _time.perf_counter()
    floor_meta = None
    needs_meta = bool(
        filters.get("camera_group")
        or filters.get("building")
        or filters.get("floor")
        or filters.get("site")
    )
    if needs_meta and not lean:
        from app.services.location_store import list_buildings

        floor_meta = build_floor_group_meta(await list_buildings())
    location_ms = (_time.perf_counter() - t_loc0) * 1000

    if active_only:
        active_filter = active_camera_filter(False)
    elif include_inactive:
        active_filter = {}
    else:
        active_filter = active_camera_filter(False)

    query = merge_query(
        _location_filters(filters, floor_meta=floor_meta),
        active_filter,
        build_access_filter(user) if user is not None else {},
    )

    id_list = filters.get("ids") or []
    if id_list:
        from bson import ObjectId
        from bson.errors import InvalidId

        oids = []
        for raw_id in id_list:
            try:
                oids.append(ObjectId(str(raw_id).strip()))
            except (InvalidId, TypeError):
                continue
        if oids:
            query = merge_query(query, {"_id": {"$in": oids}})

    projection = None if management else _LIVE_CAMERA_PROJECTION
    cameras: List[dict] = []
    t_mongo0 = _time.perf_counter()
    cursor = camera_collection.find(query, projection).sort("name", 1)
    page_meta = None
    limit_val = filters.get("limit")
    offset_val = max(0, int(filters.get("offset") or 0))
    if limit_val is not None:
        limit_n = int(limit_val)
        total_n = await camera_collection.count_documents(query)
        page_meta = {"total": total_n, "limit": limit_n, "offset": offset_val}
        cursor = cursor.skip(offset_val).limit(limit_n)
    async for cam in cursor:
        cameras.append(cam)
    mongo_ms = (_time.perf_counter() - t_mongo0) * 1000

    t_map0 = _time.perf_counter()
    if management:
        items = [_camera_management_item(c) for c in cameras]
    else:
        admin = is_admin(user)
        items = [_camera_list_item(c, admin=admin) for c in cameras]
    mapping_ms = (_time.perf_counter() - t_map0) * 1000

    # Temporary TASK-1 timing — Live View lean path only.
    if lean and not management:
        query_cameras._last_timings = {  # type: ignore[attr-defined]
            "mongo_ms": mongo_ms,
            "location_ms": location_ms,
            "mapping_ms": mapping_ms,
            "camera_count": len(items),
        }
    query_cameras._page_meta = page_meta  # type: ignore[attr-defined]
    return items


async def get_camera_groups(request) -> dict:
    user = await get_effective_user(request)
    include_inactive = (request.rel_url.query.get("includeInactive") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    if include_inactive and not is_admin(user):
        include_inactive = False

    query = merge_query(
        active_camera_filter(include_inactive),
        build_access_filter(user) if user is not None else {},
    )

    cameras: List[dict] = []
    async for cam in camera_collection.find(query).sort("name", 1):
        cameras.append(cam)

    from app.services.location_store import list_buildings

    location_buildings = await list_buildings()
    include_stats = (request.rel_url.query.get("includeStats") or "0").lower() in (
        "1",
        "true",
        "yes",
    )
    restricted = user is not None and not is_admin(user)
    if include_stats:
        from app.services.camera_management import get_management_hierarchy

        result = await get_management_hierarchy(cameras)
        payload = {
            "sites": result.get("sites") or [],
            "buildings": result["buildings"],
            "totals": result.get("totals"),
            "cameraAccess": camera_access_public(user),
        }
        if restricted:
            payload["sites"] = [
                s for s in payload["sites"] if (s.get("buildings") or [])
            ]
            payload["buildings"] = [
                b
                for b in payload["buildings"]
                if any((fg.get("cameraCount") or 0) > 0 for fg in (b.get("floorGroups") or []))
            ]
        return payload

    hierarchy = build_groups_hierarchy(
        cameras, location_buildings, cameras_only=restricted
    )
    from app.services.camera_management import _merge_configured_sites, group_hierarchy_by_site

    grouped = group_hierarchy_by_site(hierarchy)
    sites = grouped if restricted else await _merge_configured_sites(grouped)
    return {
        "sites": sites,
        "buildings": hierarchy,
        "cameraAccess": camera_access_public(user),
    }


async def get_camera_info(request=None, filters: Optional[Dict[str, Any]] = None):
    """Camera list for live view / playback with optional filters."""
    import time as _time

    from app.services.camera_management import (
        apply_stream_online_status,
        live_rows_from_memory_cache,
    )
    from app.services.stream_health import ensure_stream_health_hydrated
    from app.services.video_streaming import CAMERA_SOURCES

    t_total0 = _time.perf_counter()
    user = await get_effective_user(request) if request else None
    if request and not filters:
        filters = _parse_filters(request)

    cameras = await query_cameras(user, filters, lean=True)
    timings = dict(getattr(query_cameras, "_last_timings", {}) or {})

    t_src0 = _time.perf_counter()
    for cam_id, source in CAMERA_SOURCES.items():
        if user is not None and not is_admin(user):
            access = build_access_filter(user)
            if access:
                continue
        cameras.append({
            "id": cam_id,
            "name": source["name"],
            "displayName": source["name"],
            "online": True,
            "site": "",
            "building": "",
            "floor_group": "",
            "floor": "",
            "camera_group": "",
            "location_path": "",
            "is_active": True,
        })
    timings["sources_ms"] = (_time.perf_counter() - t_src0) * 1000

    # Live View must not wait for DB hydrate / go2rtc / RTSP probes.
    # Use in-memory health if already warm; otherwise return cameras as playable.
    t_health0 = _time.perf_counter()
    ensure_stream_health_hydrated()  # fire-and-forget background hydrate
    live_rows = live_rows_from_memory_cache(cameras)
    apply_stream_online_status(cameras, live_rows, playable_for_live=True)
    timings["health_ms"] = (_time.perf_counter() - t_health0) * 1000
    timings["go2rtc_ms"] = 0.0  # no go2rtc worker fan-out on this path

    for_playback = (
        request
        and (request.rel_url.query.get("forPlayback") or "").lower() in ("1", "true", "yes")
    )
    if for_playback and is_admin(user) and await has_unmapped_recordings():
        cameras.append(legacy_playback_camera_item())

    timings["total_ms"] = (_time.perf_counter() - t_total0) * 1000
    timings["camera_count"] = len(cameras)
    logger.info(
        "[camera-list] mongo_ms=%.1f location_ms=%.1f go2rtc_ms=%.1f health_ms=%.1f "
        "mapping_ms=%.1f total_ms=%.1f camera_count=%s",
        timings.get("mongo_ms", 0),
        timings.get("location_ms", 0),
        timings.get("go2rtc_ms", 0),
        timings.get("health_ms", 0),
        timings.get("mapping_ms", 0),
        timings.get("total_ms", 0),
        timings.get("camera_count", 0),
    )
    get_camera_info._last_timings = timings  # type: ignore[attr-defined]
    return cameras


async def get_configured_cameras_for_user(request) -> List[dict]:
    user = await get_effective_user(request)
    filters = _parse_filters(request)
    q = request.rel_url.query
    if is_admin(user):
        include_param = (q.get("includeInactive") or "true").lower()
        filters["include_inactive"] = include_param in ("1", "true", "yes")
    else:
        filters["include_inactive"] = False
    cameras = await query_cameras(user, filters, management=True)
    from app.services.camera_management import (
        _load_go2rtc_context,
        apply_stream_online_status,
    )
    from app.services.recording_schedule_store import recording_schedule

    stream_errors, live_rows = await _load_go2rtc_context(cameras)
    apply_stream_online_status(cameras, live_rows)

    live_status_filter = (filters.get("live_status") or "").strip().lower()
    online_filter = filters.get("online")
    if online_filter is True or live_status_filter == "online":
        cameras = [c for c in cameras if c.get("liveStatus") == "online"]
    elif online_filter is False or live_status_filter == "offline":
        cameras = [c for c in cameras if c.get("liveStatus") == "offline" or c.get("confirmedOffline")]

    schedule = dict(recording_schedule)
    for item in cameras:
        cid = str(item.get("_id") or item.get("id") or "")
        uid = item.get("camera_uid") or item.get("cameraUid") or ""
        item["recordingActive"] = bool(schedule.get(cid))
        if item.get("confirmedOffline"):
            item["lastError"] = stream_errors.get(cid) or stream_errors.get(uid) or item.get("lastError")
        else:
            item["lastError"] = None
        if item.get("is_active") is False:
            item["liveStatus"] = "disabled"
            item["confirmedOffline"] = False
            item["alertEligible"] = False
            item["lastError"] = None
            item["online"] = False
        elif not item.get("liveStatus"):
            item["liveStatus"] = "online" if item.get("online") else "offline"
        item["alertEligible"] = bool(item.get("confirmedOffline"))

    page_meta = getattr(query_cameras, "_page_meta", None)
    if page_meta:
        return {"items": cameras, **page_meta}
    return cameras


async def backfill_camera_locations() -> int:
    """Assign / refresh location fields from camera name (Cam1–13 → 6th, Cam14–23 → 7th)."""
    updated = 0
    async for cam in camera_collection.find({}):
        name = cam.get("name") or ""
        inferred = infer_camera_group_from_name(name)
        base_group = inferred or (cam.get("camera_group") or "")
        if base_group:
            loc = location_fields_for_group(base_group)
        else:
            loc = default_location_for_camera(name, {})

        patch: Dict[str, Any] = {}
        for key in (
            "site",
            "building",
            "floor_group",
            "floor",
            "camera_group",
            "location_path",
        ):
            new_val = loc.get(key, "")
            if new_val and cam.get(key) != new_val:
                patch[key] = new_val

        if cam.get("is_active") is None:
            patch["is_active"] = True

        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1

    if updated:
        logger.info("Backfilled location/active fields for %s camera(s)", updated)
    return updated


async def get_discovery_subnet_options() -> List[str]:
    all_db = await get_all_cameras_from_db()
    ips = [cam.get("ip_address") for cam in all_db if cam.get("ip_address")]
    return subnets_from_camera_ips(ips)


async def scan_cameras(request=None, *, subnet: Optional[str] = None):
    user = await get_effective_user(request)
    configured_raw = await query_cameras(
        user,
        {"include_inactive": is_admin(user)},
        management=True,
    )
    configured = [{k: v for k, v in cam.items() if k != "password"} for cam in configured_raw]

    all_db = await get_all_cameras_from_db()
    configured_ips = {
        normalize_discovery_ip(cam.get("ip_address"))
        for cam in all_db
        if cam.get("ip_address")
    }

    try:
        result = await discover_cameras_full(
            configured_ips=configured_ips,
            subnet=subnet,
        )
    except ValueError as exc:
        return {"error": str(exc)}, 400

    return {
        "configured": configured,
        "discovered": result["discovered"],
        "ws_discovery_count": result["ws_discovery_count"],
        "subnet_scan_count": result["subnet_scan_count"],
        "subnet_scanned": result["subnet_scanned"],
    }, 200


async def handle_add_camera(camera_data):
    err = validate_camera_payload(camera_data, is_edit=False)
    if err:
        return {"success": False, "error": err}, 400

    ip_address = normalize_ip_address(
        camera_data.get("ip_address") or camera_data.get("ip")
    )
    if not ip_address:
        return {"success": False, "error": "IP address is required"}, 400
    camera_data["ip_address"] = ip_address

    try:
        fields = prepare_camera_fields(camera_data)
        fields = finalize_camera_fields(None, fields)
        from app.services.go2rtc_workers import ensure_camera_worker_assigned

        fields = await ensure_camera_worker_assigned(fields, existing=None)
    except ValueError as e:
        return {"success": False, "error": str(e)}, 400

    dup = await find_duplicate_camera(fields)
    if dup:
        existing_doc, conflict = dup
        logger.warning(
            "Blocked duplicate camera add: conflict=%s ip=%s existing_id=%s",
            conflict,
            ip_address,
            existing_doc.get("_id"),
        )
        return duplicate_conflict_response(existing_doc, conflict, fields)

    try:
        created = await create_camera(fields)
        await register_camera_for_recording(created["_id"])
        schedule_camera_side_effects(
            str(created["_id"]),
            existing=None,
            updated_fields=fields,
            reason="camera_add",
        )
        return public_camera_response(created), 201
    except Exception as e:
        logger.exception("Error adding camera: %s", e)
        return {"success": False, "error": "Invalid data"}, 400


async def handle_update_camera(camera_id: str, camera_data: dict):
    try:
        oid = ObjectId(camera_id)
    except (InvalidId, TypeError):
        return {"success": False, "error": "Invalid camera id"}, 400

    existing = await camera_collection.find_one({"_id": oid})
    if not existing:
        return {"success": False, "error": "Camera not found"}, 404

    if camera_data.get("password") in (None, "", "***"):
        camera_data = {**camera_data}
        camera_data.pop("password", None)

    err = validate_camera_payload(
        {**existing, **camera_data},
        is_edit=True,
    )
    if err:
        return {"success": False, "error": err}, 400

    try:
        fields = prepare_camera_fields(camera_data, existing=existing)
        fields = finalize_camera_fields(existing, fields)
    except ValueError as e:
        return {"success": False, "error": str(e)}, 400

    dup = await find_duplicate_camera(fields, exclude_id=camera_id)
    if dup:
        existing_doc, conflict = dup
        return duplicate_conflict_response(existing_doc, conflict, fields)

    preserve = {}
    for key in ("recording_storage_id", "registered_at"):
        if existing.get(key) is not None:
            preserve[key] = existing[key]
    fields.update(preserve)

    needs_stream_refresh = stream_config_changed(existing, fields)

    try:
        await camera_collection.update_one({"_id": oid}, {"$set": fields})
    except Exception as e:
        err_name = type(e).__name__
        if err_name == "DuplicateKeyError" or "duplicate key" in str(e).lower():
            ip = (fields.get("ip_address") or "").strip()
            if ip:
                by_ip = await camera_collection.find_one(
                    {"ip_address": ip, "_id": {"$ne": oid}}
                )
                if by_ip:
                    return duplicate_conflict_response(by_ip, "ip_address", fields)
            return {"success": False, "error": "A camera with these details already exists."}, 409
        raise
    if needs_stream_refresh:
        schedule_camera_side_effects(
            camera_id,
            existing=existing,
            updated_fields=fields,
            reason="camera_update",
        )
    updated = await camera_collection.find_one({"_id": oid})
    updated["_id"] = str(updated["_id"])
    return public_camera_response(updated), 200


async def handle_import_cameras(payload: dict):
    """Bulk import — upsert by IP; mark missing cameras inactive."""
    cameras_in = payload.get("cameras") or []
    if not isinstance(cameras_in, list):
        return {"error": "cameras must be an array"}, 400

    mark_missing_inactive = payload.get("markMissingInactive", True) is not False

    result = await bulk_import_cameras(
        cameras_in,
        mark_missing_inactive=mark_missing_inactive,
    )
    created = result["created"]
    updated = result["updated"]
    inactive = result["markedInactive"]
    errors = result["errors"]
    worker_ids = set(result.get("workerIds") or [])

    if created or updated or inactive:
        await apply_bulk_camera_side_effects(
            reason="camera_import",
            worker_ids=worker_ids or None,
        )

    return {
        "created": created,
        "updated": updated,
        "markedInactive": inactive,
        "errors": errors,
    }, 200
