"""Location hierarchy metadata for cameras (site / building / floor / group)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.location_store import (
    CORPORATE_OFFICE,
    DEFAULT_CORPORATE_FLOORS,
    DEFAULT_SITE_NAME,
    slugify,
)

CORPORATE_OFFICE_FLOORS: List[str] = list(DEFAULT_CORPORATE_FLOORS)

_CAM_NUM_RE = re.compile(r"^Cam(\d+)$", re.IGNORECASE)


def camera_group_for_site_building_floor(site: str, building: str, floor: str) -> str:
    """Stable camera_group, e.g. rml_6_corporate_office_ground_floor."""
    s_slug = slugify(site)
    b_slug = slugify(building)
    f_slug = slugify(floor)
    if not s_slug or not b_slug or not f_slug:
        return ""
    return f"{s_slug}_{b_slug}_{f_slug}"


def camera_group_for_building_floor(
    building: str,
    floor: str,
    *,
    site: str | None = None,
) -> str:
    site_name = (site or DEFAULT_SITE_NAME).strip()
    return camera_group_for_site_building_floor(site_name, building, floor)


def camera_group_for_floor(floor: str, *, site: str | None = None) -> str:
    return camera_group_for_building_floor(CORPORATE_OFFICE, floor, site=site)


def camera_group_key_for_document(cam: dict) -> str:
    """Stable group key for hierarchy + filters — prefer stored camera_group when set."""
    group = (cam.get("camera_group") or "").strip()
    if group:
        return group
    site = (cam.get("site") or DEFAULT_SITE_NAME).strip()
    building = (cam.get("building") or "").strip()
    floor = (cam.get("floor") or cam.get("floor_group") or "").strip()
    if building and floor:
        return camera_group_for_site_building_floor(site, building, floor)
    return ""


def location_fields_for_building_floor(
    site: str,
    building: str,
    floor: str,
    *,
    area: str = "",
) -> Dict[str, str]:
    building = (building or "").strip()
    floor = (floor or "").strip()
    site = (site or DEFAULT_SITE_NAME).strip()
    area = (area or "").strip()
    camera_group = camera_group_for_site_building_floor(site, building, floor)
    parts = [p for p in (site, building, floor, area) if p]
    return {
        "site": site,
        "building": building,
        "floor_group": floor,
        "floor": floor,
        "area": area,
        "camera_group": camera_group,
        "location_path": " / ".join(parts),
    }


def location_meta_for_floor(floor: str, *, site: str | None = None) -> Dict[str, str]:
    return location_fields_for_building_floor(
        site or DEFAULT_SITE_NAME,
        CORPORATE_OFFICE,
        floor,
    )


def build_floor_group_meta(location_buildings: List[Dict[str, Any]] | None = None) -> Dict[str, Dict[str, str]]:
    meta: Dict[str, Dict[str, str]] = {}
    for bdef in location_buildings or []:
        site = (bdef.get("site") or DEFAULT_SITE_NAME).strip()
        building = (bdef.get("building") or "").strip()
        for floor in bdef.get("floors") or []:
            floor_name = floor if isinstance(floor, str) else (floor.get("name") or "")
            fields = location_fields_for_building_floor(site, building, floor_name)
            cg = fields["camera_group"]
            if cg:
                meta[cg] = fields
    return meta


FLOOR_GROUP_META: Dict[str, Dict[str, str]] = build_floor_group_meta(
    [
        {
            "site": DEFAULT_SITE_NAME,
            "building": CORPORATE_OFFICE,
            "floors": CORPORATE_OFFICE_FLOORS,
        }
    ]
)


def infer_camera_group_from_name(name: str, *, site: str | None = None) -> Optional[str]:
    m = _CAM_NUM_RE.match((name or "").strip())
    if not m:
        return None
    num = int(m.group(1))
    site_name = site or DEFAULT_SITE_NAME
    if 1 <= num <= 13:
        return camera_group_for_building_floor(CORPORATE_OFFICE, "6th Floor", site=site_name)
    if 14 <= num <= 23:
        return camera_group_for_building_floor(CORPORATE_OFFICE, "7th Floor", site=site_name)
    return None


def location_fields_for_floor(floor: str, *, site: str | None = None) -> Dict[str, str]:
    floor = (floor or "").strip()
    if not floor:
        return {
            "site": site or DEFAULT_SITE_NAME,
            "building": CORPORATE_OFFICE,
            "floor_group": "",
            "floor": "",
            "camera_group": "",
            "location_path": "",
            "area": "",
        }
    return location_fields_for_building_floor(site or DEFAULT_SITE_NAME, CORPORATE_OFFICE, floor)


def legacy_camera_group_aliases(
    camera_group: str,
    *,
    site: str | None = None,
) -> List[str]:
    """Alternate stored camera_group values (pre site-prefix migration)."""
    aliases: List[str] = []
    group = (camera_group or "").strip()
    if not group:
        return aliases
    aliases.append(group)
    site_slug = slugify(site or DEFAULT_SITE_NAME)
    prefix = f"{site_slug}_"
    if group.startswith(prefix):
        legacy = group[len(prefix) :]
        if legacy and legacy not in aliases:
            aliases.append(legacy)
    return aliases


def location_fields_for_group(
    camera_group: str,
    *,
    floor_meta: Dict[str, Dict[str, str]] | None = None,
) -> Dict[str, str]:
    lookup = floor_meta if floor_meta is not None else FLOOR_GROUP_META
    meta = lookup.get(camera_group, {})
    return {
        "site": meta.get("site", DEFAULT_SITE_NAME),
        "building": meta.get("building", ""),
        "floor_group": meta.get("floor_group", ""),
        "floor": meta.get("floor", ""),
        "area": meta.get("area", ""),
        "camera_group": camera_group,
        "location_path": meta.get("location_path", camera_group),
    }


def default_location_for_camera(
    name: str,
    overrides: Optional[Dict[str, Any]] = None,
    *,
    floor_meta: Dict[str, Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    overrides = overrides or {}
    group = (overrides.get("camera_group") or "").strip()
    if not group:
        group = infer_camera_group_from_name(name) or ""
    fields = location_fields_for_group(group, floor_meta=floor_meta) if group else {
        "site": overrides.get("site", DEFAULT_SITE_NAME),
        "building": overrides.get("building", ""),
        "floor_group": overrides.get("floor_group", ""),
        "floor": overrides.get("floor", ""),
        "camera_group": "",
        "location_path": overrides.get("location_path", ""),
        "area": overrides.get("area", ""),
    }
    building = (overrides.get("building") or fields.get("building") or "").strip()
    floor = (overrides.get("floor") or fields.get("floor") or "").strip()
    site = (overrides.get("site") or fields.get("site") or DEFAULT_SITE_NAME).strip()
    area = (overrides.get("area") or fields.get("area") or "").strip()
    if building and floor:
        fields.update(location_fields_for_building_floor(site, building, floor, area=area))
    for key in ("site", "building", "floor_group", "floor", "area", "camera_group", "location_path"):
        val = overrides.get(key)
        if val is not None and str(val).strip():
            fields[key] = str(val).strip()
    if fields.get("camera_group") and not fields.get("location_path"):
        fields["location_path"] = location_fields_for_group(
            fields["camera_group"], floor_meta=floor_meta
        ).get("location_path", fields["camera_group"])
    return fields


def _floor_order_for_building(
    building_name: str,
    location_buildings: List[Dict[str, Any]] | None,
) -> Dict[str, int]:
    if not location_buildings:
        return {f: i for i, f in enumerate(CORPORATE_OFFICE_FLOORS)}
    for bdef in location_buildings:
        if (bdef.get("building") or "").strip() == building_name:
            floors = bdef.get("floors") or []
            names = [f if isinstance(f, str) else f.get("name", "") for f in floors]
            return {f: i for i, f in enumerate(names)}
    return {}


def _floor_sort_key(entry: Dict[str, Any], floor_order: Dict[str, int]) -> tuple[int, str]:
    label = (entry.get("floor_group") or entry.get("floor") or "").strip()
    return (floor_order.get(label, 999), label)


def _ensure_configured_floors(
    buildings: Dict[str, Dict[str, Any]],
    location_buildings: List[Dict[str, Any]] | None,
) -> None:
    for bdef in location_buildings or []:
        site = (bdef.get("site") or DEFAULT_SITE_NAME).strip()
        building = (bdef.get("building") or "").strip()
        if not building:
            continue
        key = f"{site}::{building}"
        if key not in buildings:
            buildings[key] = {
                "site": site,
                "building": building,
                "floorGroups": {},
            }
        fg = buildings[key]["floorGroups"]
        for floor in bdef.get("floors") or []:
            floor_name = floor if isinstance(floor, str) else (floor.get("name") or "")
            meta = location_fields_for_building_floor(site, building, floor_name)
            group_key = meta["camera_group"]
            if not group_key or group_key in fg:
                continue
            fg[group_key] = {
                "floor_group": meta["floor_group"],
                "floor": meta["floor"],
                "camera_group": group_key,
                "location_path": meta["location_path"],
                "cameraCount": 0,
            }


def build_groups_hierarchy(
    cameras: List[dict],
    location_buildings: List[Dict[str, Any]] | None = None,
    *,
    cameras_only: bool = False,
) -> List[dict]:
    floor_meta = build_floor_group_meta(location_buildings)
    buildings: Dict[str, Dict[str, Any]] = {}

    if not cameras_only:
        _ensure_configured_floors(buildings, location_buildings)

    for cam in cameras:
        group_key = camera_group_key_for_document(cam)
        if not group_key:
            continue
        meta = floor_meta.get(group_key, {})
        site = (meta.get("site") or cam.get("site") or DEFAULT_SITE_NAME).strip()
        building = (meta.get("building") or cam.get("building") or "").strip() or "Unassigned"
        key = f"{site}::{building}"
        if key not in buildings:
            buildings[key] = {
                "site": site,
                "building": building,
                "floorGroups": {},
            }
        fg = buildings[key]["floorGroups"]
        if group_key not in fg:
            fg[group_key] = {
                "floor_group": meta.get("floor_group") or cam.get("floor_group", group_key),
                "floor": meta.get("floor") or cam.get("floor", ""),
                "camera_group": group_key,
                "location_path": meta.get("location_path") or cam.get("location_path", group_key),
                "cameraCount": 0,
            }
        fg[group_key]["cameraCount"] += 1

    config_order = [
        f"{b.get('site', DEFAULT_SITE_NAME)}::{b.get('building', '')}"
        for b in (location_buildings or [])
    ]

    def building_sort_key(entry_key: str) -> tuple[int, int, int, str]:
        entry = buildings[entry_key]
        bname = (entry.get("building") or "").strip()
        total = sum((fg.get("cameraCount") or 0) for fg in (entry.get("floorGroups") or {}).values())
        corp_prio = 0 if bname.casefold() == CORPORATE_OFFICE.casefold() else 1
        try:
            config_idx = config_order.index(entry_key)
        except ValueError:
            config_idx = 999
        return (corp_prio, -total, config_idx, entry_key)

    result = []
    for entry_key in sorted(buildings.keys(), key=building_sort_key):
        entry = buildings[entry_key]
        floor_order = _floor_order_for_building(entry["building"], location_buildings)
        floor_groups = sorted(
            entry["floorGroups"].values(),
            key=lambda x: _floor_sort_key(x, floor_order),
        )
        if cameras_only:
            floor_groups = [fg for fg in floor_groups if (fg.get("cameraCount") or 0) > 0]
            if not floor_groups:
                continue
        result.append({
            "site": entry["site"],
            "building": entry["building"],
            "floorGroups": floor_groups,
        })
    return result


def build_sites_hierarchy(
    cameras: List[dict],
    location_buildings: List[Dict[str, Any]] | None = None,
    *,
    cameras_only: bool = False,
) -> List[dict]:
    """Site → building → floor tree for management UI."""
    buildings_tree = build_groups_hierarchy(
        cameras, location_buildings, cameras_only=cameras_only
    )
    by_site: Dict[str, Dict[str, Any]] = {}
    for entry in buildings_tree:
        site = entry.get("site") or DEFAULT_SITE_NAME
        if site not in by_site:
            by_site[site] = {"site": site, "buildings": []}
        by_site[site]["buildings"].append(
            {
                "building": entry["building"],
                "floorGroups": entry["floorGroups"],
            }
        )
    return list(by_site.values())
