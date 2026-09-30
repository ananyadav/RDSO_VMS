import asyncio, sys
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.camera_locations import build_groups_hierarchy, build_floor_group_meta, camera_group_key_for_document
from app.services.camera_service import _location_filters, active_camera_filter, merge_query
from app.services.location_store import list_buildings

async def main():
    buildings = await list_buildings()
    floor_meta = build_floor_group_meta(buildings)
    q = merge_query(_location_filters({"site": "RML - 1"}, floor_meta=floor_meta), active_camera_filter(False))
    queried = [c async for c in camera_collection.find(q, {"ip_address":1,"site":1,"camera_group":1,"building":1,"floor":1})]
    cams = [c async for c in camera_collection.find(active_camera_filter(False))]
    hier = build_groups_hierarchy(cams, buildings, cameras_only=False)
    hier_ips = set()
    for b in hier:
        if (b.get("site") or "") != "RML - 1":
            continue
        # rebuild which cams land in RML-1 hier
        pass
    # cams that hierarchy places in RML-1
    for c in cams:
        gk = camera_group_key_for_document(c)
        meta = floor_meta.get(gk) or {}
        site = (meta.get("site") or c.get("site") or "").strip()
        if site == "RML - 1":
            hier_ips.add(c.get("ip_address"))
    q_ips = {c.get("ip_address") for c in queried}
    extra = q_ips - hier_ips
    missing = hier_ips - q_ips
    print("queried", len(q_ips), "hier", len(hier_ips), "extra", len(extra), "missing", len(missing))
    for ip in sorted(extra):
        c = next(x for x in queried if x.get("ip_address")==ip)
        gk = c.get("camera_group")
        meta = floor_meta.get(gk) or {}
        print("EXTRA", ip, "site=", c.get("site"), "cg=", gk, "meta_site=", meta.get("site"), "b=", c.get("building"), "f=", c.get("floor"))

asyncio.run(main())
