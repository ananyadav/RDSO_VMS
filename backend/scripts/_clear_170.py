import asyncio
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection
from app.services.camera_management import invalidate_go2rtc_context_cache

async def main():
    res = await camera_collection.update_one(
        {"ip_address": "192.168.46.170"},
        {"$set": {
            "stream_health_ok": True,
            "stream_health_alarm": False,
            "stream_health_strikes": 0,
            "stream_health_category": "online",
            "stream_health_message": "",
            "stream_health_checked_at": datetime.now(timezone.utc),
        }},
    )
    print("cleared_170", res.modified_count)
    invalidate_go2rtc_context_cache()

asyncio.run(main())
