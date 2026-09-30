import asyncio
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.rtsp_utils import sync_camera_rtsp_urls, mask_rtsp_url

async def main():
    # Try admin (not admin1) for .170 — ONVIF cam may use same user as plant Hikvisions
    cam = await camera_collection.find_one({"ip_address": "192.168.46.170"})
    if not cam:
        print("missing 170"); return
    updates = {"username": "admin"}
    cam2 = dict(cam)
    cam2["username"] = "admin"
    synced = sync_camera_rtsp_urls(cam2)
    for k in ("main_rtsp_url", "sub_rtsp_url"):
        if synced.get(k):
            updates[k] = synced[k]
            print(k, mask_rtsp_url(synced[k]))
    await camera_collection.update_one({"_id": cam["_id"]}, {"$set": updates})
    print("updated 170 username to admin")

    # For 156: force RTSP URL rebuild from stored user/pass
    cam156 = await camera_collection.find_one({"ip_address": "192.168.46.156"})
    synced156 = sync_camera_rtsp_urls(dict(cam156))
    u156 = {}
    for k in ("main_rtsp_url", "sub_rtsp_url"):
        if synced156.get(k) and synced156.get(k) != cam156.get(k):
            u156[k] = synced156[k]
            print("156", k, mask_rtsp_url(synced156[k]))
    if u156:
        await camera_collection.update_one({"_id": cam156["_id"]}, {"$set": u156})
        print("156 urls refreshed")
    else:
        print("156 urls unchanged (credentials may be wrong on device)")

asyncio.run(main())
