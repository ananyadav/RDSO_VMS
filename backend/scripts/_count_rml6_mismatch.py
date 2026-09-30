import asyncio, sys
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.camera_locations import camera_group_key_for_document, build_groups_hierarchy
from app.services.location_store import list_buildings

SITE = "RML - 6"

async def main():
    q = {"is_active": {"$ne": False}}
    cams = [c async for c in camera_collection.find(q)]
    site_cams = []
    for c in cams:
        site = (c.get("site") or "").strip()
        if site == SITE or site.upper().startswith("RML") and "6" in site:
            # also check location_path / building
            pass
        if site == SITE:
            site_cams.append(c)
    # broader: site field variants
    variants = {}
    for c in cams:
        s = (c.get("site") or "").strip() or "(empty)"
        variants[s] = variants.get(s, 0) + 1
    print("SITE_VARIANTS", sorted(variants.items(), key=lambda x: -x[1])[:20])

    site_cams = [c for c in cams if (c.get("site") or "").strip() == SITE]
    with_group = [c for c in site_cams if camera_group_key_for_document(c)]
    without_group = [c for c in site_cams if not camera_group_key_for_document(c)]
    print("site_exact", SITE, "total", len(site_cams), "with_group", len(with_group), "without_group", len(without_group))

    # hierarchy count for this site
    buildings = await list_buildings()
    hier = build_groups_hierarchy(cams, buildings, cameras_only=False)
    site_hier = [b for b in hier if (b.get("site") or "").strip() == SITE]
    hier_count = sum(sum(fg.get("cameraCount") or 0 for fg in (b.get("floorGroups") or [])) for b in site_hier)
    print("hierarchy_count", hier_count, "buildings", len(site_hier))

    # sample without group
    for c in without_group[:8]:
        print("NO_GROUP", c.get("ip_address"), "building=", c.get("building"), "floor=", c.get("floor"), "cg=", c.get("camera_group"), "path=", (c.get("location_path") or "")[:50])

asyncio.run(main())
