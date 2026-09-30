import asyncio, sys
from pathlib import Path
from collections import Counter, defaultdict
from dotenv import load_dotenv
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.camera_locations import (
    camera_group_key_for_document,
    build_groups_hierarchy,
    build_floor_group_meta,
)
from app.services.location_store import list_buildings

SITE = "RML - 6"

async def main():
    cams = [c async for c in camera_collection.find({"is_active": {"$ne": False}})]
    site_cams = [c for c in cams if (c.get("site") or "").strip() == SITE]
    buildings = await list_buildings()
    floor_meta = build_floor_group_meta(buildings)
    hier = build_groups_hierarchy(cams, buildings, cameras_only=False)

    # total counted in hierarchy across all sites
    by_site = Counter()
    for b in hier:
        n = sum(fg.get("cameraCount") or 0 for fg in (b.get("floorGroups") or []))
        by_site[b.get("site") or "?"] += n
    print("HIER_BY_SITE", dict(by_site))
    print("HIER_TOTAL", sum(by_site.values()), "DB_ACTIVE", len(cams))

    # For site cams: compare meta.site vs cam.site
    meta_site_mismatch = []
    counted_keys = set()
    for b in hier:
        if (b.get("site") or "").strip() != SITE:
            continue
        for fg in b.get("floorGroups") or []:
            counted_keys.add(fg.get("camera_group"))

    # which site cams land outside RML-6 hierarchy
    outside = []
    for c in site_cams:
        gk = camera_group_key_for_document(c)
        meta = floor_meta.get(gk) or {}
        resolved_site = (meta.get("site") or c.get("site") or "").strip()
        if resolved_site != SITE:
            outside.append((c.get("ip_address"), gk, resolved_site, meta.get("building"), c.get("building"), c.get("camera_group")))
        elif gk not in counted_keys:
            # same site but not in counted keys? weird
            outside.append((c.get("ip_address"), gk, "SAME_SITE_NOT_COUNTED", meta.get("building"), c.get("building"), c.get("camera_group")))

    print("outside_or_mismatch", len(outside))
    # group by resolved site / reason
    reasons = Counter(x[2] for x in outside)
    print("reasons", dict(reasons))
    # sample
    for row in outside[:12]:
        print("SAMPLE", row)

    # camera_group duplicates? count per group in site
    gk_counts = Counter(camera_group_key_for_document(c) for c in site_cams)
    # hierarchy counts per group for RML-6
    hier_gk = {}
    for b in hier:
        if (b.get("site") or "").strip() != SITE: continue
        for fg in b.get("floorGroups") or []:
            hier_gk[fg.get("camera_group")] = fg.get("cameraCount")

    diffs = []
    for gk, n in gk_counts.items():
        h = hier_gk.get(gk)
        if h is None:
            diffs.append((gk, n, 0, "missing_from_rml6_hier"))
        elif h != n:
            diffs.append((gk, n, h, "count_mismatch"))
    print("group_diffs", len(diffs))
    for d in diffs[:20]:
        print("DIFF", d)
    missing_n = sum(d[1] for d in diffs if d[3]=="missing_from_rml6_hier")
    print("missing_cam_sum", missing_n)

asyncio.run(main())
