import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")

from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc

IPS = [
    "192.168.46.150",
    "192.168.46.151",
    "192.168.46.152",
    "192.168.46.153",
    "192.168.46.154",
    "192.168.46.155",
    "192.168.46.156",
    "192.168.46.157",
    "192.168.46.158",
    "192.168.13.7",
]


async def frame_ok(session, base, stream):
    if not base or not stream:
        return "no_base", 0
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            body = await resp.read()
            return f"HTTP{resp.status}", len(body)
    except Exception as e:
        return type(e).__name__, 0


async def main():
    async with aiohttp.ClientSession() as session:
        for ip in IPS:
            c = await camera_collection.find_one({"ip_address": ip})
            if not c:
                print(json.dumps({"ip": ip, "status": "NOT_IN_DB"}))
                continue
            base = await get_api_url_for_camera_doc(c)
            sn = stream_name(c.get("camera_uid") or "", "sub")
            st, n = await frame_ok(session, base, sn)
            print(
                json.dumps(
                    {
                        "ip": ip,
                        "alarm": c.get("stream_health_alarm"),
                        "ok": c.get("stream_health_ok"),
                        "cat": c.get("stream_health_category"),
                        "msg": (c.get("stream_health_message") or "")[:100],
                        "user": c.get("username"),
                        "proto": c.get("protocol"),
                        "frame": st,
                        "bytes": n,
                        "stream": sn,
                        "go2rtc": base,
                    }
                )
            )


asyncio.run(main())
