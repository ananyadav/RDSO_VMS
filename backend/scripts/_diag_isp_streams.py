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
    "192.168.46.152",
    "192.168.46.156",
    "192.168.46.158",
    "192.168.13.7",
]


async def main():
    async with aiohttp.ClientSession() as session:
        for ip in IPS:
            c = await camera_collection.find_one({"ip_address": ip})
            if not c:
                print(ip, "missing")
                continue
            base = await get_api_url_for_camera_doc(c)
            sn = stream_name(c.get("camera_uid") or "", "sub")
            url = f"{base.rstrip('/')}/api/streams?src={sn}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    text = await resp.text()
                    data = json.loads(text) if text else {}
            except Exception as e:
                print(json.dumps({"ip": ip, "err": str(e)}))
                continue
            # go2rtc may return {stream: {...}} or just the stream dict
            info = data.get(sn) if isinstance(data, dict) and sn in data else data
            producers = []
            if isinstance(info, dict):
                for p in info.get("producers") or []:
                    if isinstance(p, dict):
                        producers.append(
                            {
                                "url": (p.get("url") or "")[:80],
                                "error": (p.get("error") or p.get("err") or "")[:120],
                                "medias": len(p.get("medias") or []),
                            }
                        )
                consumers = len(info.get("consumers") or [])
            else:
                consumers = 0
            print(
                json.dumps(
                    {
                        "ip": ip,
                        "stream": sn,
                        "go2rtc": base,
                        "consumers": consumers,
                        "producers": producers,
                        "sub_rtsp": (c.get("sub_rtsp_url") or "")[:70].replace(
                            str(c.get("password") or ""), "***"
                        ),
                    }
                )
            )


asyncio.run(main())
