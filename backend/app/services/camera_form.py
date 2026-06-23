"""Normalize, validate, and prepare camera documents for add/edit/import."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection
from app.services.camera_locations import (
    CORPORATE_OFFICE,
    location_fields_for_building_floor,
)
from app.services.location_store import DEFAULT_SITE_NAME
from app.services.camera_uid import camera_display_name, make_camera_uid
from app.services.rtsp_utils import apply_rtsp_urls, mask_rtsp_url

CORPORATE_DEFAULTS: Dict[str, Any] = {
    "site": DEFAULT_SITE_NAME,
    "building": CORPORATE_OFFICE,
    "floor_group": "Ground Floor",
    "protocol": "HIKVISION",
    "port": 554,
    "type": "rtsp",
    "main_channel": "101",
    "sub_channel": "102",
    "preview_channel": "103",
    "recording_channel": "102",
    "is_active": True,
    "worker_id": "worker-1",
    "live_provider": "go2rtc",
    "ptz": False,
    "online": False,
    "activity": False,
}

REQUIRED_FIELDS = ("name", "ip_address", "username", "password", "protocol", "building", "floor")


def _str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()


def _protocol(val: str) -> str:
    p = _str(val, "HIKVISION").upper()
    if p in ("HIK", "HIKVISION"):
        return "HIKVISION"
    if p == "ONVIF":
        return "ONVIF"
    return "CUSTOM"


def normalize_ip_address(ip: Any) -> str:
    return _str(ip)


def resolved_ip_address(cam: dict) -> str:
    return normalize_ip_address(cam.get("ip_address") or cam.get("ip"))


def ip_duplicate_clauses(ip: str) -> List[dict]:
    """Match cameras by ip_address, legacy ip, camera_uid, or IP embedded in RTSP URLs."""
    ip = normalize_ip_address(ip)
    if not ip:
        return []

    clauses: List[dict] = [
        {"ip_address": ip},
        {"ip": ip},
    ]
    uid = make_camera_uid(ip)
    if uid:
        clauses.append({"camera_uid": uid})

    escaped = re.escape(ip)
    clauses.append({"ip_address": {"$regex": f"^\\s*{escaped}\\s*$"}})
    clauses.append({"ip": {"$regex": f"^\\s*{escaped}\\s*$"}})
    clauses.append({"sub_rtsp_url": {"$regex": f"@{escaped}[:/]", "$options": "i"}})
    clauses.append({"rtsp_url": {"$regex": f"@{escaped}[:/]", "$options": "i"}})
    clauses.append({"main_rtsp_url": {"$regex": f"@{escaped}[:/]", "$options": "i"}})
    return clauses


async def find_existing_by_ip(
    ip: str,
    exclude_oid: Optional[ObjectId],
) -> Optional[dict]:
    clauses = ip_duplicate_clauses(ip)
    if not clauses:
        return None
    return await _find_camera_excluding({"$or": clauses}, exclude_oid)


def existing_camera_summary(cam: dict) -> dict:
    ip = resolved_ip_address(cam)
    return {
        "id": str(cam["_id"]),
        "name": cam.get("name", ""),
        "ip_address": ip,
        "building": cam.get("building", ""),
        "floor": cam.get("floor", ""),
        "floor_group": cam.get("floor_group", ""),
        "camera_group": cam.get("camera_group", ""),
        "location_path": cam.get("location_path", ""),
        "is_active": cam.get("is_active") is not False,
        "protocol": cam.get("protocol", ""),
        "camera_uid": cam.get("camera_uid", "") or make_camera_uid(ip) or "",
    }


async def _find_camera_excluding(
    query: dict,
    exclude_oid: Optional[ObjectId],
) -> Optional[dict]:
    if exclude_oid:
        query = {"$and": [query, {"_id": {"$ne": exclude_oid}}]}
    return await camera_collection.find_one(query)


async def find_duplicate_camera(
    fields: dict,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[Tuple[dict, str]]:
    """Return (existing camera, conflict field). IP/camera_uid is checked first."""
    exclude_oid = None
    if exclude_id:
        try:
            exclude_oid = ObjectId(exclude_id)
        except (InvalidId, TypeError):
            pass

    ip = normalize_ip_address(fields.get("ip_address") or fields.get("ip"))
    if ip:
        existing = await find_existing_by_ip(ip, exclude_oid)
        if existing:
            return existing, "ip_address"

    name = _str(fields.get("name"))
    if name:
        existing = await _find_camera_excluding({"name": name}, exclude_oid)
        if existing:
            return existing, "name"

    main_url = _str(fields.get("main_rtsp_url"))
    if main_url:
        existing = await _find_camera_excluding({"main_rtsp_url": main_url}, exclude_oid)
        if existing:
            return existing, "main_rtsp_url"

    sub_url = _str(fields.get("sub_rtsp_url"))
    if sub_url:
        existing = await _find_camera_excluding({"sub_rtsp_url": sub_url}, exclude_oid)
        if existing:
            return existing, "sub_rtsp_url"

    return None


def validate_camera_payload(data: dict, *, is_edit: bool = False) -> Optional[str]:
    for key in REQUIRED_FIELDS:
        if key == "password" and is_edit and not data.get("password"):
            continue
        val = data.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            return f"{key.replace('_', ' ')} is required"
    return None


def prepare_camera_fields(
    camera_data: dict,
    *,
    existing: Optional[dict] = None,
) -> dict:
    """Merge defaults, location, channels, RTSP URLs, and camera_uid."""
    existing = existing or {}
    merged = {**CORPORATE_DEFAULTS, **existing, **camera_data}

    ip_address = _str(camera_data.get("ip_address") or camera_data.get("ip") or existing.get("ip_address"))
    if not ip_address:
        raise ValueError("IP address is required")

    protocol = _protocol(camera_data.get("protocol") or existing.get("protocol"))
    building = _str(camera_data.get("building") or existing.get("building"))
    floor = _str(camera_data.get("floor") or existing.get("floor"))
    site = _str(camera_data.get("site") or existing.get("site") or DEFAULT_SITE_NAME)
    if building and floor:
        merged.update(location_fields_for_building_floor(site, building, floor))

    for key in (
        "site", "building", "floor_group", "floor", "area",
        "camera_group", "location_path", "name", "model", "username", "worker_id", "live_provider",
    ):
        if camera_data.get(key) is not None:
            merged[key] = _str(camera_data.get(key))

    merged["ip_address"] = ip_address
    merged["port"] = int(camera_data.get("port") or existing.get("port") or 554)
    merged["protocol"] = protocol
    merged["type"] = _str(camera_data.get("type") or existing.get("type") or "rtsp")

    for ch_key, legacy in (
        ("main_channel", "main_channel"),
        ("sub_channel", "recording_channel"),
        ("preview_channel", "preview_channel"),
    ):
        val = camera_data.get(ch_key) or camera_data.get(legacy) or existing.get(ch_key) or existing.get(legacy)
        if val is not None:
            merged[ch_key] = _str(val)
            if ch_key == "sub_channel":
                merged["recording_channel"] = _str(val)

    if "password" in camera_data:
        merged["password"] = _str(camera_data.get("password"))
    elif existing.get("password") is not None:
        merged["password"] = existing.get("password")

    if "is_active" in camera_data:
        merged["is_active"] = camera_data.get("is_active") is not False
    if "ptz" in camera_data:
        merged["ptz"] = bool(camera_data.get("ptz"))
    if "online" in camera_data:
        merged["online"] = bool(camera_data.get("online"))
    if "activity" in camera_data:
        merged["activity"] = bool(camera_data.get("activity"))

    merged["camera_uid"] = make_camera_uid(ip_address) or ""

    manual_rtsp = protocol in ("ONVIF", "CUSTOM")
    if manual_rtsp:
        for url_key in ("main_rtsp_url", "sub_rtsp_url", "preview_rtsp_url"):
            if camera_data.get(url_key) is not None:
                merged[url_key] = _str(camera_data.get(url_key))
            elif existing.get(url_key):
                merged[url_key] = existing.get(url_key)
        merged["rtsp_url_source"] = _str(
            camera_data.get("rtsp_url_source") or ("manual" if protocol == "CUSTOM" else "onvif")
        )
    else:
        merged = apply_rtsp_urls(merged, force_auto=True)

    merged["display_name"] = _str(camera_data.get("display_name")) or camera_display_name(merged)
    return merged


def duplicate_conflict_response(
    existing: dict,
    conflict: str,
    fields: dict,
) -> Tuple[dict, int]:
    active = existing.get("is_active") is not False
    loc = existing.get("location_path") or existing.get("floor") or "unknown location"
    existing_name = existing.get("name") or "camera"
    existing_ip = resolved_ip_address(existing) or normalize_ip_address(fields.get("ip_address"))

    if conflict == "name":
        requested_name = _str(fields.get("name")) or existing_name
        if active:
            message = (
                f"Camera name '{requested_name}' is already in use by "
                f"{existing_name} ({existing_ip}) in {loc}."
            )
        else:
            message = (
                f"Camera name '{requested_name}' is already assigned to a disabled camera "
                f"({existing_name}, {existing_ip}) in {loc}. "
                f"Edit or reactivate that camera instead of reusing this name."
            )
    elif conflict in ("main_rtsp_url", "sub_rtsp_url"):
        label = "main" if conflict == "main_rtsp_url" else "sub"
        if active:
            message = (
                f"The {label} RTSP URL is already used by {existing_name} "
                f"({existing_ip}) in {loc}."
            )
        else:
            message = (
                f"The {label} RTSP URL belongs to disabled camera {existing_name} "
                f"({existing_ip}) in {loc}. Edit or reactivate that camera instead."
            )
    else:
        ip = _str(fields.get("ip_address")) or existing_ip
        uid = existing.get("camera_uid") or ""
        if active:
            message = f"Camera IP {ip} already exists as {existing_name} in {loc}."
        else:
            message = (
                f"Camera IP {ip} already exists but is disabled. "
                f"It belongs to {loc}. Please reactivate/edit the existing camera."
            )
        if uid:
            message = message.replace(f"IP {ip}", f"IP {ip} ({uid})")

    return {
        "success": False,
        "code": "DUPLICATE_CAMERA",
        "conflict": conflict,
        "message": message,
        "existingCamera": existing_camera_summary(existing),
    }, 409


def public_camera_response(cam: dict) -> dict:
    out = dict(cam)
    if "password" in out and out["password"]:
        out["password"] = "***"
    for url_key in ("main_rtsp_url", "sub_rtsp_url", "preview_rtsp_url", "rtsp_url"):
        if out.get(url_key):
            out[f"{url_key}_masked"] = mask_rtsp_url(out[url_key])
    return out
