import asyncio
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
import aiohttp
from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name, ensure_go2rtc_streams
from app.services.go2rtc_workers import get_api_url_for_camera_doc

async def main():
    print("syncing go2rtc streams...")
    result = await ensure_go2rtc_streams()
    print({k: result.get(k) for k in ("ok", "added", "updated", "removed", "workers", "error") if k in result or result.get(k) is not None})
    async with aiohttp.ClientSession() as session:
        for ip in ("192.168.46.150", "192.168.46.151", "192.168.46.156", "192.168.46.170", "192.168.46.153"):
            cam = await camera_collection.find_one({"ip_address": ip})
            base = await get_api_url_for_camera_doc(cam)
            sn = stream_name(cam.get("camera_uid"), "sub")
            url = f"{base.rstrip('/')}/api/frame.jpeg?src={sn}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    n = len(await resp.read()) if resp.status == 200 else 0
                    print(ip, "frame", resp.status, n, "user", cam.get("username"), "alarm", cam.get("stream_health_alarm"))
            except Exception as e:
                print(ip, "err", e)

asyncio.run(main())
