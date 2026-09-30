import asyncio, sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.camera_locations import build_groups_hierarchy, build_floor_group_meta
from app.services.camera_service import _location_filters, active_camera_filter, merge_query
from app.services.location_store import list_buildings

async def main():
    buildings = await list_buildings()
    floor_meta = build_floor_group_meta(buildings)
    cams = [c async for c in camera_collection.find(active_camera_filter(False))]
    hier = build_groups_hierarchy(cams, buildings, cameras_only=False)
    by_site = Counter()
    for b in hier:
        n = sum(fg.get("cameraCount") or 0 for fg in (b.get("floorGroups") or []))
        by_site[b.get("site") or "?"] += n
    print("HIER", dict(by_site))
    variants = Counter((c.get("site") or "").strip() for c in cams)
    print("DB_SITE", dict(variants))
    for site in ["RML - 6", "RML - 1", "RML - 3", "RML - 7"]:
        q = merge_query(_location_filters({"site": site}, floor_meta=floor_meta), active_camera_filter(False))
        n = await camera_collection.count_documents(q)
        print(f"QUERY {site} = {n} (hier={by_site.get(site,0)}) match={n==by_site.get(site,0)}")

asyncio.run(main())
