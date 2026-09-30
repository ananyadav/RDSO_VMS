import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
import aiohttp
from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc

async def ping(ip, timeout=2.5):
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, 554), timeout=timeout)
        w.close()
        try: await w.wait_closed()
        except Exception: pass
        return True
    except Exception:
        return False

async def frame_bytes(session, base, stream):
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200:
                return -resp.status
            return len(await resp.read())
    except Exception:
        return -1

async def main():
    # 1) All cameras currently alarmed offline in DB
    alarmed = [c async for c in camera_collection.find(
        {"stream_health_alarm": True, "is_active": {"$ne": False}},
        {"ip_address":1,"name":1,"display_name":1,"location_path":1,"building":1,"site":1,
         "stream_health_category":1,"stream_health_message":1,"protocol":1,"worker_id":1,"camera_uid":1,"username":1}
    ).sort([("location_path",1),("ip_address",1)])]
    print(f"ALARMED_ACTIVE_TOTAL={len(alarmed)}")

    # Group by location
    from collections import Counter
    print("by_location", Counter((c.get("location_path") or "?") for c in alarmed).most_common(20))

    async with aiohttp.ClientSession() as session:
        rows = []
        for c in alarmed:
            ip = c.get("ip_address") or ""
            uid = c.get("camera_uid") or ""
            base = await get_api_url_for_camera_doc(c)
            sn = stream_name(uid, "sub") if uid else ""
            tcp = await ping(ip) if ip else False
            nbytes = await frame_bytes(session, base, sn) if base and sn else -1
            status = "FRAMES_OK" if nbytes and nbytes > 1000 else ("NO_TCP" if not tcp else f"NO_FRAME({nbytes})")
            rows.append({
                "ip": ip,
                "name": c.get("display_name") or c.get("name"),
                "path": c.get("location_path"),
                "proto": c.get("protocol"),
                "w": c.get("worker_id"),
                "cat": c.get("stream_health_category"),
                "msg": (c.get("stream_health_message") or "")[:70],
                "user": c.get("username"),
                "status": status,
            })
            print(json.dumps(rows[-1]))

        false_offline = [r for r in rows if r["status"] == "FRAMES_OK"]
        real_down = [r for r in rows if r["status"] != "FRAMES_OK"]
        print(f"\nSUMMARY false_offline_badge={len(false_offline)} real_down={len(real_down)}")

asyncio.run(main())
