#!/usr/bin/env python3
"""Mark Task 7 test cameras healthy so Live View will attempt streams."""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402

IPS = [
    "192.168.41.106",
    "192.168.41.13",
    "192.168.41.23",
    "192.168.41.24",
    "192.168.41.41",
]


async def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    for ip in IPS:
        r = await camera_collection.update_one(
            {"ip_address": ip},
            {
                "$set": {
                    "stream_health_ok": True,
                    "stream_health_alarm": False,
                    "stream_health_strikes": 0,
                    "stream_health_category": "online",
                    "stream_health_message": "",
                    "stream_health_checked_at": now,
                }
            },
        )
        print(ip, "matched", r.matched_count, "modified", r.modified_count)


if __name__ == "__main__":
    asyncio.run(main())
