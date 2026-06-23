import logging
import asyncio
import socket
import ipaddress
import re
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from bson.errors import InvalidId

from app.core.database import (
    camera_collection,
    get_all_cameras_from_db,
    create_camera,
    upsert_camera_by_ip,
    mark_cameras_inactive_not_in_ips,
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
from wsdiscovery import WSDiscovery
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CAM_NUM_RE = re.compile(r"^Cam(\d+)$", re.IGNORECASE)


def _camera_list_item(cam: dict, *, admin: bool = False) -> dict:
    is_active = cam.get("is_active")
    if is_active is None:
        is_active = True
    streamable = bool(is_active)
    ip = (cam.get("ip_address") or "").strip()
    uid = cam.get("camera_uid") or make_camera_uid(ip) or ""
    item = {
        "id": str(cam["_id"]) if isinstance(cam.get("_id"), ObjectId) else cam.get("_id"),
        "cameraUid": uid,
        "name": cam.get("name", ""),
        "displayName": camera_display_name(cam),
        "online": streamable,
        "ptz": bool(cam.get("ptz", False)),
        "activity": bool(cam.get("activity", False)),
        "site": cam.get("site", ""),
        "building": cam.get("building", ""),
        "floor_group": cam.get("floor_group", ""),
        "floor": cam.get("floor", ""),
        "camera_group": cam.get("camera_group", ""),
        "location_path": cam.get("location_path", ""),
        "is_active": bool(is_active),
    }
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
        "sub_channel": cam.get("sub_channel") or cam.get("recording_channel", "102"),
        "preview_channel": cam.get("preview_channel", "103"),
        "main_rtsp_url": cam.get("main_rtsp_url", ""),
        "sub_rtsp_url": cam.get("sub_rtsp_url", ""),
        "preview_rtsp_url": cam.get("preview_rtsp_url", ""),
        "rtsp_url_source": cam.get("rtsp_url_source", ""),
        "worker_id": cam.get("worker_id", ""),
        "live_provider": cam.get("live_provider", "go2rtc"),
        "online": bool(cam.get("online", False)),
        "ptz": bool(cam.get("ptz", False)),
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
    if online_raw in ("1", "true", "yes"):
        filters["online"] = True
    elif online_raw in ("0", "false", "no"):
        filters["online"] = False
    filters["include_inactive"] = include_inactive
    return filters


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
    if filters.get("site"):
        q["site"] = filters["site"]
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
    online = filters.get("online")
    if online is True:
        q["online"] = True
    elif online is False:
        and_clauses.append({"$or": [{"online": False}, {"online": {"$exists": False}}]})
    if and_clauses:
        q["$and"] = and_clauses
    return q


async def query_cameras(
    user: Optional[dict],
    filters: Optional[Dict[str, Any]] = None,
    *,
    management: bool = False,
) -> List[dict]:
    """Query cameras with location filters, active filter, and access control."""
    filters = filters or {}
    include_inactive = bool(filters.get("include_inactive")) and is_admin(user)
    active_only = bool(filters.get("active_only"))

    floor_meta = None
    if filters.get("camera_group") or filters.get("building") or filters.get("floor"):
        from app.services.location_store import list_buildings

        floor_meta = build_floor_group_meta(await list_buildings())

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

    cameras: List[dict] = []
    async for cam in camera_collection.find(query).sort("name", 1):
        cameras.append(cam)

    if management:
        return [_camera_management_item(c) for c in cameras]
    admin = is_admin(user)
    return [_camera_list_item(c, admin=admin) for c in cameras]


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
    include_stats = (request.rel_url.query.get("includeStats") or "1").lower() in (
        "1",
        "true",
        "yes",
    )
    if include_stats:
        from app.services.camera_management import get_management_hierarchy

        result = await get_management_hierarchy(cameras)
        return {
            "sites": result.get("sites") or [],
            "buildings": result["buildings"],
            "totals": result.get("totals"),
            "cameraAccess": camera_access_public(user),
        }

    hierarchy = build_groups_hierarchy(cameras, location_buildings)
    return {
        "buildings": hierarchy,
        "cameraAccess": camera_access_public(user),
    }


async def get_camera_info(request=None, filters: Optional[Dict[str, Any]] = None):
    """Camera list for live view / playback with optional filters."""
    from app.services.video_streaming import CAMERA_SOURCES

    user = await get_effective_user(request) if request else None
    if request and not filters:
        filters = _parse_filters(request)

    cameras = await query_cameras(user, filters)

    for cam_id, source in CAMERA_SOURCES.items():
        if user is not None and not is_admin(user):
            access = build_access_filter(user)
            if access:
                continue
        cameras.append({
            "id": cam_id,
            "name": source["name"],
            "online": True,
            "ptz": source.get("ptz", False),
            "activity": False,
            "site": "",
            "building": "",
            "floor_group": "",
            "floor": "",
            "camera_group": "",
            "location_path": "",
            "is_active": True,
        })

    for_playback = (
        request
        and (request.rel_url.query.get("forPlayback") or "").lower() in ("1", "true", "yes")
    )
    if for_playback and is_admin(user) and await has_unmapped_recordings():
        cameras.append(legacy_playback_camera_item())

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
    from app.services.camera_management import _load_go2rtc_context
    from app.services.recording_schedule_store import recording_schedule

    stream_errors, live_rows = await _load_go2rtc_context()
    schedule = dict(recording_schedule)
    for item in cameras:
        cid = str(item.get("_id") or item.get("id") or "")
        uid = item.get("camera_uid") or item.get("cameraUid") or ""
        row = live_rows.get(cid) or {}
        if item.get("is_active") is not False:
            item["online"] = bool(item.get("online")) or bool(
                row.get("subOnline") or row.get("mainOnline")
            )
        item["recordingActive"] = bool(schedule.get(cid))
        item["lastError"] = stream_errors.get(cid) or stream_errors.get(uid)
        item["liveStatus"] = (
            "offline"
            if item.get("is_active") is False
            else ("online" if item.get("online") else "offline")
        )
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


def extract_ip(url):
    parsed = urlparse(url)
    return parsed.hostname


async def scan_cameras(request=None):
    user = await get_effective_user(request)
    configured_raw = await query_cameras(
        user,
        {"include_inactive": is_admin(user)},
        management=True,
    )
    configured = [{k: v for k, v in cam.items() if k != "password"} for cam in configured_raw]

    def _run_wsdiscovery():
        wsd = WSDiscovery()
        wsd.start()
        try:
            return wsd.searchServices()
        finally:
            wsd.stop()

    try:
        services = await asyncio.to_thread(_run_wsdiscovery)
    except Exception as e:
        logger.warning(f"WS-Discovery failed: {e}")
        services = []

    discovered = []
    for service in services:
        types = service.getTypes()
        if 'NetworkVideoTransmitter' in str(types):
            xaddrs = service.getXAddrs()
            if xaddrs:
                url = xaddrs[0]
                ip = extract_ip(url)
                name = f"ONVIF Camera at {ip}" if ip else "Discovered Camera"
                discovered.append({
                    "name": name,
                    "ip_address": ip or "",
                    "type": "rtsp"
                })

    port_discovered = await port_scan_cameras()
    discovered.extend(port_discovered)

    return {"configured": configured, "discovered": discovered}


async def port_scan_cameras():
    networks = [
        ipaddress.IPv4Network('192.168.41.0/24'),
        ipaddress.IPv4Network('192.168.48.0/24'),
        ipaddress.IPv4Network('169.254.20.0/24'),
        ipaddress.IPv4Network('192.168.1.0/24'),
        ipaddress.IPv4Network('10.0.0.0/24'),
    ]
    configured = await get_all_cameras_from_db()
    configured_ips = {cam['ip_address'] for cam in configured}

    tasks = []
    port = 554
    timeout = 2

    async def check_port(ip):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = await asyncio.get_running_loop().run_in_executor(None, sock.connect_ex, (str(ip), port))
            sock.close()
            if result == 0:
                return str(ip)
        except Exception:
            pass
        return None

    for network in networks:
        for ip in network.hosts():
            tasks.append(check_port(ip))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    discovered_ips = [ip for ip in results if ip and ip not in configured_ips]

    discovered = []
    for ip in discovered_ips:
        discovered.append({
            "name": f"Discovered Camera at {ip}",
            "ip_address": ip,
            "type": "rtsp"
        })

    return discovered


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

    try:
        await camera_collection.update_one({"_id": oid}, {"$set": fields})
    except Exception as e:
        err_name = type(e).__name__
        if err_name == "DuplicateKeyError" or "duplicate key" in str(e).lower():
            by_name = await camera_collection.find_one(
                {"name": fields.get("name"), "_id": {"$ne": oid}}
            )
            if by_name:
                return duplicate_conflict_response(by_name, "name", fields)
            return {"success": False, "error": "A camera with these details already exists."}, 409
        raise
    updated = await camera_collection.find_one({"_id": oid})
    updated["_id"] = str(updated["_id"])
    return public_camera_response(updated), 200


async def handle_import_cameras(payload: dict):
    """Bulk import — upsert by IP; mark missing cameras inactive."""
    cameras_in = payload.get("cameras") or []
    if not isinstance(cameras_in, list):
        return {"error": "cameras must be an array"}, 400

    mark_missing_inactive = payload.get("markMissingInactive", True) is not False
    active_ips: set = set()
    created = 0
    updated = 0
    errors: List[str] = []

    for row in cameras_in:
        if not isinstance(row, dict):
            errors.append("invalid row")
            continue
        ip = (row.get("ip_address") or row.get("ip") or "").strip()
        if not ip:
            errors.append("row missing ip_address")
            continue
        active_ips.add(ip)
        name = (row.get("name") or "").strip()
        if name:
            row["name"] = name
        loc = default_location_for_camera(name or ip, row)
        row.update(loc)
        row["is_active"] = row.get("is_active", True) is not False
        try:
            fields = prepare_camera_fields(row)
            result = await upsert_camera_by_ip({**row, **fields})
            if result.get("created"):
                created += 1
            else:
                updated += 1
        except Exception as exc:
            errors.append(f"{ip}: {exc}")

    inactive = 0
    if mark_missing_inactive and active_ips:
        inactive = await mark_cameras_inactive_not_in_ips(active_ips)

    return {
        "created": created,
        "updated": updated,
        "markedInactive": inactive,
        "errors": errors,
    }, 200
