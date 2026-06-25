"""Force-resync RTSP URLs for every camera from current credentials."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.core.database import camera_collection
from app.services.go2rtc_service import ensure_go2rtc_streams
from app.services.rtsp_utils import rtsp_url_credentials_stale, sync_camera_rtsp_urls


async def main() -> None:
    total = 0
    updated = 0
    stale_before = 0

    async for cam in camera_collection.find({}):
        ip = (cam.get("ip_address") or "").strip()
        if not ip:
            continue
        total += 1
        if rtsp_url_credentials_stale(cam):
            stale_before += 1

        synced = sync_camera_rtsp_urls(cam)
        if not synced.get("sub_rtsp_url"):
            print(f"SKIP (no sub URL): {cam.get('name')} ({ip})")
            continue

        patch = {
            k: synced[k]
            for k in (
                "main_rtsp_url",
                "sub_rtsp_url",
                "preview_rtsp_url",
                "rtsp_url",
                "rtsp_url_source",
                "main_channel",
                "sub_channel",
                "recording_channel",
                "preview_channel",
            )
            if k in synced
        }
        await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
        updated += 1

    print(f"Cameras processed: {total}")
    print(f"Stale before resync: {stale_before}")
    print(f"Cameras updated: {updated}")

    result = await ensure_go2rtc_streams()
    print(f"go2rtc sync: {'ok' if result.get('ok') else result.get('error', 'failed')}")
    if result.get("streamCount"):
        print(f"go2rtc streams: {result.get('streamCount')}")


if __name__ == "__main__":
    asyncio.run(main())
