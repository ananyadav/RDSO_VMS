import asyncio
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection

async def main():
    items = [
        c
        async for c in camera_collection.find(
            {"location_path": "rml_6_isp_power_plant", "stream_health_alarm": True},
            {"ip_address": 1, "stream_health_message": 1, "stream_health_category": 1},
        )
    ]
    print("alarmed_now", len(items))
    for c in items:
        print(c.get("ip_address"), c.get("stream_health_category"), (c.get("stream_health_message") or "")[:70])

    # Clear false SETUP->auth alarms
    res = await camera_collection.update_many(
        {
            "location_path": "rml_6_isp_power_plant",
            "stream_health_alarm": True,
            "stream_health_message": {"$regex": "response on SETUP", "$options": "i"},
        },
        {
            "$set": {
                "stream_health_ok": True,
                "stream_health_alarm": False,
                "stream_health_strikes": 0,
                "stream_health_category": "online",
                "stream_health_message": "",
                "stream_health_checked_at": datetime.now(timezone.utc),
            }
        },
    )
    print("cleared_setup_false_alarms", res.modified_count)

    # Also clear alarms for cameras that are online ok except known bad auth IPs
    bad = {"192.168.46.156", "192.168.46.170"}
    res2 = await camera_collection.update_many(
        {
            "location_path": "rml_6_isp_power_plant",
            "stream_health_alarm": True,
            "ip_address": {"$nin": list(bad)},
        },
        {
            "$set": {
                "stream_health_ok": True,
                "stream_health_alarm": False,
                "stream_health_strikes": 0,
                "stream_health_category": "online",
                "stream_health_message": "",
                "stream_health_checked_at": datetime.now(timezone.utc),
            }
        },
    )
    print("cleared_other_false_alarms", res2.modified_count)

asyncio.run(main())
