#!/usr/bin/env python3
"""Diagnose specific camera IPs: DB, RTSP reachability, go2rtc (per-worker)."""
import asyncio
import re
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection
from app.services.camera_uid import make_camera_uid
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc, normalize_worker_id
from app.services.rtsp_utils import mask_rtsp_url, rtsp_url_credentials_stale, sync_camera_rtsp_urls

IPS = sys.argv[1:] or ["192.168.46.7", "192.168.46.8", "192.168.46.9"]


async def ping_tcp(ip: str, port: int = 554, timeout: float = 3) -> tuple[bool, str]:
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True, "open"
    except asyncio.TimeoutError:
        return False, "timeout"
    except OSError as exc:
        return False, str(exc)


async def go2rtc_probe(session: aiohttp.ClientSession, base_url: str, stream: str) -> tuple:
    url = f"{base_url.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.read()
            return resp.status, len(body), resp.headers.get("Content-Type", "")
    except Exception as exc:
        return None, 0, str(exc)


async def fetch_streams(session: aiohttp.ClientSession, base_url: str) -> dict:
    try:
        async with session.get(
            f"{base_url.rstrip('/')}/api/streams",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            return await resp.json() if resp.status == 200 else {}
    except Exception:
        return {}


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        for ip in IPS:
            print("=" * 60)
            print("IP", ip)
            cam = await camera_collection.find_one({"ip_address": ip})
            if not cam:
                cam = await camera_collection.find_one(
                    {"sub_rtsp_url": {"$regex": re.escape(ip)}}
                )
            if not cam:
                print("  NOT FOUND in MongoDB")
                reachable, reason = await ping_tcp(ip)
                print(f"  RTSP port 554: {reachable} ({reason})")
                continue

            name = cam.get("name")
            active = cam.get("is_active") is not False
            protocol = cam.get("protocol")
            uid = cam.get("camera_uid") or make_camera_uid(ip)
            wid = normalize_worker_id(cam.get("worker_id")) or 1
            api_url = await get_api_url_for_camera_doc(cam)
            sub_key = stream_name(uid, "sub")
            main_key = stream_name(uid, "main")
            streams_data = await fetch_streams(session, api_url)

            print(f"  name: {name}")
            print(f"  active: {active}  protocol: {protocol}  uid: {uid}  worker: {wid}")
            print(f"  go2rtc api: {api_url}")
            print(f"  sub registered: {sub_key in streams_data}")
            print(f"  main registered: {main_key in streams_data}")

            synced = sync_camera_rtsp_urls(dict(cam))
            stale = rtsp_url_credentials_stale(cam)
            print(f"  credentials stale: {stale}")
            for key in ("main_rtsp_url", "sub_rtsp_url"):
                val = cam.get(key) or synced.get(key) or ""
                masked = mask_rtsp_url(val) if val else "MISSING"
                print(f"  {key}: {masked}")

            reachable, reason = await ping_tcp(ip)
            print(f"  RTSP port 554: {reachable} ({reason})")

            if sub_key in streams_data:
                print(f"  go2rtc sub info: {streams_data[sub_key]}")

            for label, sk in [("sub", sub_key), ("main", main_key)]:
                if sk not in streams_data:
                    print(f"  frame probe {label}: stream not in worker {wid} config")
                    continue
                status, nbytes, ctype = await go2rtc_probe(session, api_url, sk)
                print(f"  frame probe {label}: status={status} bytes={nbytes} type={ctype}")


if __name__ == "__main__":
    asyncio.run(main())
