"""Optional `locations` collection — canonical site/building/floor hierarchy."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.database import database
from app.services.camera_locations import (
    build_groups_hierarchy,
    camera_group_for_site_building_floor,
    location_fields_for_building_floor,
)
from app.services.location_store import DEFAULT_SITE_NAME, list_buildings, slugify

logger = logging.getLogger(__name__)

locations_collection = database.get_collection("locations")


def _floor_sort_key(floor_name: str) -> int:
    lower = floor_name.lower()
    if lower.startswith("ground"):
        return 0
    for token in ("1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th"):
        if token in lower:
            return int(token[0])
    return 999


async def sync_locations_catalog() -> int:
    """Upsert location docs from persisted building/floor config."""
    buildings = await list_buildings()
    now = datetime.now(timezone.utc).isoformat()
    upserted = 0

    for bdef in buildings:
        site = (bdef.get("site") or DEFAULT_SITE_NAME).strip()
        building = (bdef.get("building") or site).strip()
        if not building:
            continue

        site_slug = slugify(site) or slugify(building)
        building_slug = slugify(building)

        for doc in (
            {
                "name": site,
                "type": "site",
                "slug": site_slug,
                "parent_slug": "",
                "path": site,
                "sort_order": 0,
                "is_active": True,
                "updated_at": now,
            },
            {
                "name": building,
                "type": "building",
                "slug": building_slug,
                "parent_slug": site_slug,
                "path": f"{site} / {building}",
                "sort_order": 0,
                "is_active": True,
                "updated_at": now,
            },
        ):
            res = await locations_collection.update_one(
                {"slug": doc["slug"], "type": doc["type"]},
                {"$set": doc},
                upsert=True,
            )
            if res.upserted_id or res.modified_count:
                upserted += 1

        for floor in bdef.get("floors") or []:
            floor_name = floor if isinstance(floor, str) else (floor.get("name") or "")
            floor = floor_name.strip()
            if not floor:
                continue
            group = camera_group_for_site_building_floor(site, building, floor)
            fields = location_fields_for_building_floor(site, building, floor)
            floor_doc = {
                "name": floor,
                "type": "floor",
                "slug": group,
                "parent_slug": building_slug,
                "path": fields["location_path"],
                "camera_group": group,
                "building": building,
                "site": site,
                "sort_order": _floor_sort_key(floor),
                "is_active": True,
                "updated_at": now,
            }
            res = await locations_collection.update_one(
                {"slug": group, "type": "floor"},
                {"$set": floor_doc},
                upsert=True,
            )
            if res.upserted_id or res.modified_count:
                upserted += 1

    if upserted:
        logger.info("[LOCATIONS] Synced %s location catalog document(s)", upserted)
    return upserted


async def list_location_catalog() -> List[Dict[str, Any]]:
    cursor = locations_collection.find({"is_active": {"$ne": False}}).sort(
        [("type", 1), ("sort_order", 1), ("name", 1)]
    )
    return await cursor.to_list(length=500)
