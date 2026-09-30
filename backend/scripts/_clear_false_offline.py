import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
import aiohttp
from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc
from app.services.camera_management import invalidate_go2rtc_context_cache

async def frame_ok(session, base, stream):
    if not base or not stream:
        return False
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return resp.status == 200 and len(await resp.read()) > 1000
    except Exception:
        return False

async def main():
    alarmed = [c async for c in camera_collection.find(
        {"stream_health_alarm": True, "is_active": {"$ne": False}},
        {"_id":1,"ip_address":1,"camera_uid":1,"location_path":1,"stream_health_message":1}
    )]
    cleared_ips = []
    async with aiohttp.ClientSession() as session:
        for c in alarmed:
            base = await get_api_url_for_camera_doc(c)
            sn = stream_name(c.get("camera_uid") or "", "sub")
            if await frame_ok(session, base, sn):
                await camera_collection.update_one(
                    {"_id": c["_id"]},
                    {"$set": {
                        "stream_health_ok": True,
                        "stream_health_alarm": False,
                        "stream_health_strikes": 0,
                        "stream_health_category": "online",
                        "stream_health_message": "",
                        "stream_health_checked_at": datetime.now(timezone.utc),
                    }},
                )
                cleared_ips.append(c.get("ip_address"))
    invalidate_go2rtc_context_cache()
    print(f"cleared={len(cleared_ips)}")
    print(json.dumps(cleared_ips))
    still = await camera_collection.count_documents({"stream_health_alarm": True, "is_active": {"$ne": False}})
    print(f"still_alarmed={still}")
    pp = [c async for c in camera_collection.find(
        {"location_path": "rml_6_isp_power_plant", "stream_health_alarm": True},
        {"ip_address":1,"stream_health_message":1}
    )]
    print("power_plant_still_offline", [(c.get("ip_address"), (c.get("stream_health_message") or "")[:50]) for c in pp])

asyncio.run(main())
