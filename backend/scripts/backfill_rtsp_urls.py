import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.core.database import backfill_all_camera_rtsp_urls, camera_collection


async def main() -> None:
    updated = await backfill_all_camera_rtsp_urls()
    print(f"backfilled {updated} camera(s)")
    cam = await camera_collection.find_one({"ip_address": "192.168.46.12"})
    if cam:
        print("46.12 password:", cam.get("password"))
        print("46.12 sub:", cam.get("sub_rtsp_url"))


if __name__ == "__main__":
    asyncio.run(main())
