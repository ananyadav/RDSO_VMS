"""Persist site → building → floor hierarchy in MongoDB."""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.database import camera_collection, database

logger = logging.getLogger(__name__)

_settings_collection = database.get_collection("system_settings")
_SETTINGS_ID = "locations"

DEFAULT_SITE_NAME = "RML - 6"
CORPORATE_OFFICE = "Corporate Office"
HEALTHCARE_CLINIC = "Healthcare Clinic"
CLINIC_FLOOR = "Clinic"

DEFAULT_CORPORATE_FLOORS: List[str] = [
    "Ground Floor",
    "1st Floor",
    "2nd Floor",
    "3rd Floor",
    "4th Floor",
    "5th Floor",
    "6th Floor",
    "7th Floor",
]

DEFAULT_SITES: List[Dict[str, Any]] = [
    {
        "id": "rml_6",
        "name": DEFAULT_SITE_NAME,
        "is_active": True,
        "buildings": [
            {
                "id": "corporate_office",
                "name": CORPORATE_OFFICE,
                "is_active": True,
                "floors": [
                    {"name": f, "is_active": True} for f in DEFAULT_CORPORATE_FLOORS
                ],
            },
            {"id": "parking", "name": "Parking", "is_active": True, "floors": []},
            {
                "id": "healthcare_clinic",
                "name": HEALTHCARE_CLINIC,
                "is_active": True,
                "floors": [{"name": CLINIC_FLOOR, "is_active": True}],
            },
            {"id": "gym", "name": "Gym", "is_active": True, "floors": []},
        ],
    }
]


class LocationStoreError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")


def site_id_for(name: str) -> str:
    return slugify(name) or "site"


def building_id_for(site_name: str, building_name: str) -> str:
    return slugify(building_name) or slugify(site_name) or "building"


def _normalize_floor_entry(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        name = raw.strip()
        return {"name": name, "is_active": bool(name)}
    name = (raw.get("name") or "").strip()
    return {
        "name": name,
        "is_active": raw.get("is_active", True) is not False,
    }


def _normalize_floor_list(floors: List[Any]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for raw in floors or []:
        entry = _normalize_floor_entry(raw)
        name = entry["name"]
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _normalize_building(doc: Dict[str, Any], *, site_name: str) -> Dict[str, Any]:
    name = (doc.get("name") or doc.get("building") or "").strip()
    if not name:
        raise LocationStoreError("building name is required")
    bid = (doc.get("id") or "").strip() or building_id_for(site_name, name)
    return {
        "id": bid,
        "name": name,
        "is_active": doc.get("is_active", True) is not False,
        "floors": _normalize_floor_list(list(doc.get("floors") or [])),
    }


def _normalize_site(doc: Dict[str, Any]) -> Dict[str, Any]:
    name = (doc.get("name") or doc.get("site") or "").strip()
    if not name:
        raise LocationStoreError("site name is required")
    sid = (doc.get("id") or "").strip() or site_id_for(name)
    buildings = [
        _normalize_building(b, site_name=name) for b in (doc.get("buildings") or [])
    ]
    return {
        "id": sid,
        "name": name,
        "is_active": doc.get("is_active", True) is not False,
        "buildings": buildings,
    }


def _legacy_buildings_to_sites(buildings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert flat buildings[] config to sites[]."""
    by_site: Dict[str, Dict[str, Any]] = {}
    for entry in buildings:
        site_name = (entry.get("site") or entry.get("building") or DEFAULT_SITE_NAME).strip()
        sid = site_id_for(site_name)
        if sid not in by_site:
            by_site[sid] = {
                "id": sid,
                "name": site_name,
                "is_active": True,
                "buildings": [],
            }
        floors_raw = entry.get("floors") or []
        by_site[sid]["buildings"].append(
            {
                "id": entry.get("id") or building_id_for(site_name, entry.get("building", "")),
                "name": (entry.get("building") or site_name).strip(),
                "is_active": entry.get("is_active", True) is not False,
                "floors": floors_raw,
            }
        )
    return list(by_site.values()) or copy.deepcopy(DEFAULT_SITES)


async def load_sites(*, include_inactive: bool = False) -> List[Dict[str, Any]]:
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if not doc:
        return copy.deepcopy(DEFAULT_SITES)
    sites_raw = doc.get("sites")
    if sites_raw:
        sites = [_normalize_site(s) for s in sites_raw]
    else:
        sites = [_normalize_site(s) for s in _legacy_buildings_to_sites(doc.get("buildings") or [])]
    if not sites:
        return copy.deepcopy(DEFAULT_SITES)
    if include_inactive:
        return sites
    out: List[Dict[str, Any]] = []
    for site in sites:
        if site.get("is_active") is False:
            continue
        buildings = [
            {
                **b,
                "floors": [f for f in b.get("floors") or [] if f.get("is_active") is not False],
            }
            for b in site.get("buildings") or []
            if b.get("is_active") is not False
        ]
        out.append({**site, "buildings": buildings})
    return out


async def save_sites(sites: List[Dict[str, Any]]) -> None:
    normalized = [_normalize_site(s) for s in sites]
    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {
            "$set": {
                "sites": normalized,
                "schema_version": 2,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {"buildings": ""},
        },
        upsert=True,
    )


def flatten_buildings(
    sites: List[Dict[str, Any]],
    *,
    include_inactive: bool = False,
) -> List[Dict[str, Any]]:
    """Flat building list for backward-compatible APIs."""
    flat: List[Dict[str, Any]] = []
    for site in sites:
        if not include_inactive and site.get("is_active") is False:
            continue
        for bdef in site.get("buildings") or []:
            if not include_inactive and bdef.get("is_active") is False:
                continue
            floors = [
                f["name"]
                for f in bdef.get("floors") or []
                if include_inactive or f.get("is_active") is not False
            ]
            flat.append(
                {
                    "id": bdef["id"],
                    "site": site["name"],
                    "site_id": site["id"],
                    "building": bdef["name"],
                    "is_active": bdef.get("is_active", True) is not False,
                    "floors": floors,
                }
            )
    return flat


async def list_buildings(*, include_inactive: bool = False) -> List[Dict[str, Any]]:
    sites = await load_sites(include_inactive=include_inactive)
    return flatten_buildings(sites, include_inactive=include_inactive)


async def get_building(building_name: str) -> Dict[str, Any] | None:
    name = (building_name or "").strip()
    if not name:
        return None
    for entry in await list_buildings(include_inactive=True):
        if entry["building"].casefold() == name.casefold():
            return entry
    return None


def _find_site(sites: List[Dict[str, Any]], site_id: str) -> Optional[Dict[str, Any]]:
    sid = (site_id or "").strip()
    for site in sites:
        if site["id"] == sid or site["name"].casefold() == sid.casefold():
            return site
    return None


async def bootstrap_location_config() -> None:
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if not doc:
        await save_sites(copy.deepcopy(DEFAULT_SITES))
        logger.info("[LOCATIONS] Seeded default RML - 6 site hierarchy")
        return
    if doc.get("schema_version", 1) < 2 or not doc.get("sites"):
        sites = _legacy_buildings_to_sites(doc.get("buildings") or [])
        if not any(s.get("name") == DEFAULT_SITE_NAME for s in sites):
            sites = copy.deepcopy(DEFAULT_SITES)
        await save_sites(sites)
        logger.info("[LOCATIONS] Migrated location config to site hierarchy (v2)")
    await ensure_rml6_default_structure()


async def ensure_rml6_default_structure() -> None:
    """Ensure RML - 6 contains Corporate Office + ancillary buildings; merge legacy site rows."""
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if doc and doc.get("rml6_structure_v3"):
        return

    sites = await load_sites(include_inactive=True)
    rml = _find_site(sites, "rml_6") or _find_site(sites, DEFAULT_SITE_NAME)
    corporate_floors: List[Dict[str, Any]] = copy.deepcopy(
        DEFAULT_SITES[0]["buildings"][0]["floors"]
    )

    for site in sites:
        for b in site.get("buildings") or []:
            if b["name"].casefold() == CORPORATE_OFFICE.casefold() and b.get("floors"):
                corporate_floors = list(b.get("floors") or corporate_floors)

    if not rml:
        rml = copy.deepcopy(DEFAULT_SITES[0])
        sites.append(rml)
    else:
        rml["name"] = DEFAULT_SITE_NAME
        rml["id"] = rml.get("id") or "rml_6"

    by_id = {b["id"]: b for b in rml.get("buildings") or []}
    by_name = {b["name"].casefold(): b for b in rml.get("buildings") or []}

    def _ensure_building(bdef: Dict[str, Any]) -> None:
        bid = bdef["id"]
        name_key = bdef["name"].casefold()
        if name_key in by_name:
            existing = by_name[name_key]
            if bdef.get("floors") and not existing.get("floors"):
                existing["floors"] = bdef["floors"]
            return
        if bid in by_id:
            return
        rml.setdefault("buildings", []).append(copy.deepcopy(bdef))
        by_id[bid] = bdef
        by_name[name_key] = bdef

    _ensure_building(
        {
            "id": "corporate_office",
            "name": CORPORATE_OFFICE,
            "is_active": True,
            "floors": corporate_floors,
        }
    )
    for extra in DEFAULT_SITES[0]["buildings"][1:]:
        _ensure_building(extra)

    # Drop standalone "Corporate Office" site when its only building is Corporate Office
    cleaned: List[Dict[str, Any]] = []
    for site in sites:
        if site["id"] == rml["id"]:
            cleaned.append(rml)
            continue
        buildings = site.get("buildings") or []
        if (
            site["name"].casefold() == CORPORATE_OFFICE.casefold()
            and len(buildings) == 1
            and buildings[0]["name"].casefold() == CORPORATE_OFFICE.casefold()
        ):
            continue
        cleaned.append(site)

    if rml not in cleaned:
        cleaned.insert(0, rml)

    await save_sites(cleaned)
    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": {"rml6_structure_v3": True, "schema_version": 3}},
        upsert=True,
    )
    logger.info("[LOCATIONS] Reconciled RML - 6 default site structure (v3)")


async def consolidate_healthcare_into_rml6() -> int:
    """Keep only RML - 6 site; move Healthcare/Clinic cameras under Healthcare Clinic."""
    from app.services.camera_locations import location_fields_for_building_floor

    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if doc and doc.get("healthcare_consolidated_v1"):
        return 0

    sites = await load_sites(include_inactive=True)
    rml = _find_site(sites, "rml_6") or _find_site(sites, DEFAULT_SITE_NAME)
    if not rml:
        rml = copy.deepcopy(DEFAULT_SITES[0])

    clinic_floors: List[Dict[str, Any]] = []
    seen_floors: set[str] = set()
    for site in sites:
        for b in site.get("buildings") or []:
            bname = (b.get("name") or "").strip()
            if bname.casefold() not in ("clinic", HEALTHCARE_CLINIC.casefold()):
                continue
            for raw in b.get("floors") or []:
                fname = (raw.get("name") if isinstance(raw, dict) else raw or "").strip()
                if not fname:
                    continue
                key = fname.casefold()
                if key in seen_floors:
                    continue
                seen_floors.add(key)
                clinic_floors.append({"name": fname, "is_active": True})
    if not clinic_floors:
        clinic_floors = [{"name": CLINIC_FLOOR, "is_active": True}]

    buildings_out: List[Dict[str, Any]] = []
    hc_entry: Optional[Dict[str, Any]] = None
    for b in rml.get("buildings") or []:
        name = (b.get("name") or "").casefold()
        if name in (HEALTHCARE_CLINIC.casefold(), "clinic"):
            if hc_entry is None:
                hc_entry = {
                    **b,
                    "id": "healthcare_clinic",
                    "name": HEALTHCARE_CLINIC,
                    "is_active": True,
                    "floors": clinic_floors or list(b.get("floors") or []),
                }
            continue
        buildings_out.append(b)
    if hc_entry is None:
        hc_entry = {
            "id": "healthcare_clinic",
            "name": HEALTHCARE_CLINIC,
            "is_active": True,
            "floors": clinic_floors,
        }
    buildings_out.append(hc_entry)
    rml["buildings"] = buildings_out
    rml["name"] = DEFAULT_SITE_NAME
    rml["id"] = "rml_6"
    rml["is_active"] = True

    await save_sites([rml])

    updated = 0
    async for cam in camera_collection.find({}):
        site = (cam.get("site") or "").strip()
        building = (cam.get("building") or "").strip()
        is_clinic = (
            site.casefold() == "healthcare"
            or building.casefold() in ("clinic", HEALTHCARE_CLINIC.casefold())
        )
        if not is_clinic:
            continue
        floor = (cam.get("floor") or cam.get("floor_group") or CLINIC_FLOOR).strip() or CLINIC_FLOOR
        fields = location_fields_for_building_floor(DEFAULT_SITE_NAME, HEALTHCARE_CLINIC, floor)
        patch: Dict[str, Any] = {}
        for key in ("site", "building", "floor", "floor_group", "camera_group", "location_path"):
            val = fields.get(key, "")
            if val and cam.get(key) != val:
                patch[key] = val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1

    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": {"healthcare_consolidated_v1": True, "single_site_rml6": True}},
        upsert=True,
    )
    if updated:
        logger.info("[LOCATIONS] Moved %s clinic camera(s) under RML - 6 / Healthcare Clinic", updated)
    logger.info("[LOCATIONS] Consolidated to single site: RML - 6")
    return updated


async def sync_all_camera_groups() -> int:
    """Normalize camera_group / location_path from site + building + floor."""
    from app.services.camera_locations import (
        camera_group_key_for_document,
        location_fields_for_building_floor,
    )

    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if doc and doc.get("camera_groups_canonical_v1"):
        return 0

    updated = 0
    async for cam in camera_collection.find({}):
        site = (cam.get("site") or DEFAULT_SITE_NAME).strip()
        building = (cam.get("building") or "").strip()
        floor = (cam.get("floor") or cam.get("floor_group") or "").strip()
        if building and floor:
            fields = location_fields_for_building_floor(
                site, building, floor, area=(cam.get("area") or "").strip()
            )
        else:
            fields = {}
        derived = fields.get("camera_group") or camera_group_key_for_document(cam)
        if not derived:
            continue
        patch: Dict[str, Any] = {}
        if cam.get("camera_group") != derived:
            patch["camera_group"] = derived
        for key in ("site", "location_path"):
            val = fields.get(key, "")
            if val and cam.get(key) != val:
                patch[key] = val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1

    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": {"camera_groups_canonical_v1": True}},
        upsert=True,
    )
    if updated:
        logger.info("[LOCATIONS] Canonicalized camera_group for %s camera(s)", updated)
    return updated


async def migrate_corporate_office_cameras() -> int:
    """One-time: set site/path/camera_group for Corporate Office cameras."""
    from app.services.camera_locations import location_fields_for_building_floor

    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID})
    if doc and doc.get("cameras_migrated_rml6"):
        return 0

    updated = 0
    async for cam in camera_collection.find({}):
        building = (cam.get("building") or "").strip()
        site = (cam.get("site") or "").strip()
        floor = (cam.get("floor") or cam.get("floor_group") or "").strip()
        is_corporate = (
            building.casefold() == CORPORATE_OFFICE.casefold()
            or site.casefold() == CORPORATE_OFFICE.casefold()
        )
        if not is_corporate or not floor:
            continue
        fields = location_fields_for_building_floor(
            DEFAULT_SITE_NAME,
            building or CORPORATE_OFFICE,
            floor,
            area=(cam.get("area") or "").strip(),
        )
        patch: Dict[str, Any] = {}
        for key in ("site", "building", "floor_group", "floor", "camera_group", "location_path"):
            val = fields.get(key, "")
            if val and cam.get(key) != val:
                patch[key] = val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1

    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": {"cameras_migrated_rml6": True}},
        upsert=True,
    )
    if updated:
        logger.info("[LOCATIONS] Migrated %s camera(s) to RML - 6 location paths", updated)
    return updated


# --- CRUD ---


async def add_site(*, name: str) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise LocationStoreError("site name is required")
    sites = await load_sites(include_inactive=True)
    for site in sites:
        if site["name"].casefold() == name.casefold():
            raise LocationStoreError(f"Site '{name}' already exists", 409)
    entry = _normalize_site({"name": name, "buildings": []})
    sites.append(entry)
    await save_sites(sites)
    return entry


async def update_site(*, site_id: str, name: Optional[str] = None, is_active: Optional[bool] = None) -> Dict[str, Any]:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    if name is not None and name.strip():
        site["name"] = name.strip()
    if is_active is not None:
        site["is_active"] = bool(is_active)
    await save_sites(sites)
    return site


async def delete_site(*, site_id: str) -> None:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    sites = [s for s in sites if s["id"] != site["id"]]
    await save_sites(sites)


async def add_building(*, site_id: str, building: str, floors: List[str] | None = None) -> Dict[str, Any]:
    building = (building or "").strip()
    floor_list = _normalize_floor_list(list(floors or []))
    if not building:
        raise LocationStoreError("building name is required")

    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    for b in site.get("buildings") or []:
        if b["name"].casefold() == building.casefold():
            raise LocationStoreError(f"Building '{building}' already exists", 409)
    new_b = _normalize_building({"name": building, "floors": floor_list}, site_name=site["name"])
    site.setdefault("buildings", []).append(new_b)
    await save_sites(sites)
    return flatten_buildings(sites, include_inactive=True)[-1]


async def _migrate_cameras_for_building_rename(
    *,
    site_name: str,
    old_building: str,
    new_building: str,
) -> int:
    from app.services.camera_locations import location_fields_for_building_floor

    updated = 0
    async for cam in camera_collection.find({}):
        cam_site = (cam.get("site") or "").strip()
        cam_building = (cam.get("building") or "").strip()
        if cam_site.casefold() != site_name.casefold():
            continue
        if cam_building.casefold() != old_building.casefold():
            continue
        floor = (cam.get("floor") or cam.get("floor_group") or "").strip()
        area = (cam.get("area") or "").strip()
        fields = location_fields_for_building_floor(site_name, new_building, floor, area=area)
        patch: Dict[str, Any] = {}
        for key in ("building", "floor_group", "floor", "camera_group", "location_path"):
            val = fields.get(key, "")
            if val and cam.get(key) != val:
                patch[key] = val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1
    if updated:
        logger.info(
            "[LOCATIONS] Renamed building '%s' → '%s' on %s camera(s)",
            old_building,
            new_building,
            updated,
        )
    return updated


async def _migrate_cameras_for_floor_rename(
    *,
    site_name: str,
    building_name: str,
    old_floor: str,
    new_floor: str,
) -> int:
    from app.services.camera_locations import location_fields_for_building_floor

    updated = 0
    async for cam in camera_collection.find({}):
        cam_site = (cam.get("site") or "").strip()
        cam_building = (cam.get("building") or "").strip()
        cam_floor = (cam.get("floor") or cam.get("floor_group") or "").strip()
        if cam_site.casefold() != site_name.casefold():
            continue
        if cam_building.casefold() != building_name.casefold():
            continue
        if cam_floor.casefold() != old_floor.casefold():
            continue
        area = (cam.get("area") or "").strip()
        fields = location_fields_for_building_floor(site_name, building_name, new_floor, area=area)
        patch: Dict[str, Any] = {}
        for key in ("floor_group", "floor", "camera_group", "location_path"):
            val = fields.get(key, "")
            if val and cam.get(key) != val:
                patch[key] = val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1
    if updated:
        logger.info(
            "[LOCATIONS] Renamed floor '%s' → '%s' on %s camera(s)",
            old_floor,
            new_floor,
            updated,
        )
    return updated


async def update_building(
    *,
    site_id: str,
    building_id: str,
    name: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    target = None
    for b in site.get("buildings") or []:
        if b["id"] == building_id:
            target = b
            break
    if not target:
        raise LocationStoreError("Building not found", 404)
    if name is not None and name.strip():
        new_name = name.strip()
        old_name = (target.get("name") or "").strip()
        if new_name.casefold() != old_name.casefold():
            for b in site.get("buildings") or []:
                if b["id"] != building_id and (b.get("name") or "").strip().casefold() == new_name.casefold():
                    raise LocationStoreError(f"Building '{new_name}' already exists", 409)
            await _migrate_cameras_for_building_rename(
                site_name=site["name"],
                old_building=old_name,
                new_building=new_name,
            )
            target["name"] = new_name
    if is_active is not None:
        target["is_active"] = bool(is_active)
    await save_sites(sites)
    return target


async def delete_building(*, site_id: str, building_id: str) -> None:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    site["buildings"] = [b for b in site.get("buildings") or [] if b["id"] != building_id]
    await save_sites(sites)


async def add_floor(*, site_id: str, building_id: str, floor: str) -> Dict[str, Any]:
    floor = (floor or "").strip()
    if not floor:
        raise LocationStoreError("floor name is required")
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    target = None
    for b in site.get("buildings") or []:
        if b["id"] == building_id:
            target = b
            break
    if not target:
        raise LocationStoreError("Building not found", 404)
    floors = list(target.get("floors") or [])
    if any(f["name"].casefold() == floor.casefold() for f in floors):
        raise LocationStoreError(f"Floor '{floor}' already exists", 409)
    floors.append({"name": floor, "is_active": True})
    target["floors"] = floors
    await save_sites(sites)
    return target


async def update_floor(
    *,
    site_id: str,
    building_id: str,
    floor_name: str,
    new_name: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> Dict[str, Any]:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    target_b = None
    for b in site.get("buildings") or []:
        if b["id"] == building_id:
            target_b = b
            break
    if not target_b:
        raise LocationStoreError("Building not found", 404)
    floor_entry = None
    for f in target_b.get("floors") or []:
        if f["name"].casefold() == floor_name.casefold():
            floor_entry = f
            break
    if not floor_entry:
        raise LocationStoreError("Floor not found", 404)
    if new_name is not None and new_name.strip():
        renamed = new_name.strip()
        old_name = (floor_entry.get("name") or "").strip()
        if renamed.casefold() != old_name.casefold():
            for f in target_b.get("floors") or []:
                if f is not floor_entry and (f.get("name") or "").strip().casefold() == renamed.casefold():
                    raise LocationStoreError(f"Floor '{renamed}' already exists", 409)
            await _migrate_cameras_for_floor_rename(
                site_name=site["name"],
                building_name=target_b["name"],
                old_floor=old_name,
                new_floor=renamed,
            )
            floor_entry["name"] = renamed
    if is_active is not None:
        floor_entry["is_active"] = bool(is_active)
    await save_sites(sites)
    return floor_entry


async def delete_floor(*, site_id: str, building_id: str, floor_name: str) -> None:
    sites = await load_sites(include_inactive=True)
    site = _find_site(sites, site_id)
    if not site:
        raise LocationStoreError("Site not found", 404)
    for b in site.get("buildings") or []:
        if b["id"] != building_id:
            continue
        b["floors"] = [
            f for f in b.get("floors") or [] if f["name"].casefold() != floor_name.casefold()
        ]
    await save_sites(sites)
