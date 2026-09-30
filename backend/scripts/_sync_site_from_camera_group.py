"""Sync camera site/building/floor from Location Master via camera_group."""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")

from app.core.database import camera_collection
from app.services.camera_locations import (
    build_floor_group_meta,
    camera_group_key_for_document,
    location_fields_for_group,
)
from app.services.location_store import list_buildings


async def main() -> None:
    buildings = await list_buildings()
    floor_meta = build_floor_group_meta(buildings)
    updated = 0
    skipped = 0
    async for cam in camera_collection.find({"is_active": {"$ne": False}}):
        gk = camera_group_key_for_document(cam)
        if not gk or gk not in floor_meta:
            skipped += 1
            continue
        loc = location_fields_for_group(gk, floor_meta=floor_meta)
        patch = {}
        for key in ("site", "building", "floor_group", "floor", "location_path", "camera_group"):
            new_val = (loc.get(key) or "").strip()
            if new_val and (cam.get(key) or "").strip() != new_val:
                patch[key] = new_val
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1
    print(f"updated={updated} skipped_no_meta={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
