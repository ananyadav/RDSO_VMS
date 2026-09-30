"""Clear false Offline alarms for ISP Power Plant; re-sync broken streams."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc
from app.services.rtsp_utils import sync_camera_rtsp_urls


async def frame_ok(session: aiohttp.ClientSession, base: str, stream: str) -> bool:
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200:
                return False
            return len(await resp.read()) > 1000
    except Exception:
        return False


async def main() -> None:
    cams = [
        c
        async for c in camera_collection.find({"location_path": "rml_6_isp_power_plant"})
    ]
    print(f"power_plant={len(cams)}")
    cleared = 0
    still_bad = []
    async with aiohttp.ClientSession() as session:
        for cam in cams:
            ip = cam.get("ip_address")
            uid = cam.get("camera_uid") or ""
            cid = cam["_id"]
            base = await get_api_url_for_camera_doc(cam)
            sn = stream_name(uid, "sub")
            ok = await frame_ok(session, base, sn) if base and uid else False
            alarm = bool(cam.get("stream_health_alarm"))
            msg = cam.get("stream_health_message") or ""
            if ok:
                if alarm or cam.get("stream_health_ok") is False:
                    await camera_collection.update_one(
                        {"_id": cid},
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
                    cleared += 1
                    print(f"CLEARED {ip} was alarm={alarm} msg={msg[:60]!r}")
                else:
                    print(f"OK      {ip}")
            else:
                # Try refreshing RTSP URL fields into DB (no secret print)
                synced = sync_camera_rtsp_urls(dict(cam))
                updates = {}
                for key in ("main_rtsp_url", "sub_rtsp_url"):
                    if synced.get(key) and synced.get(key) != cam.get(key):
                        updates[key] = synced[key]
                if updates:
                    await camera_collection.update_one({"_id": cid}, {"$set": updates})
                    print(f"RESYNC_URLS {ip} keys={list(updates)}")
                still_bad.append((ip, msg, cam.get("protocol"), cam.get("username")))
                print(f"BAD     {ip} proto={cam.get('protocol')} msg={msg[:70]!r}")

    print(f"\ncleared_alarms={cleared} still_no_frame={len(still_bad)}")
    for row in still_bad:
        print("  ", row)


if __name__ == "__main__":
    asyncio.run(main())
