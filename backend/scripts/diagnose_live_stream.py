#!/usr/bin/env python3
"""Quick live stream diagnostic."""
import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection, user_collection  # noqa: E402
from app.services.go2rtc_service import GO2RTC_API_URL, stream_name  # noqa: E402
from app.services.camera_uid import make_camera_uid  # noqa: E402


async def main():
    admin = await user_collection.find_one({"role": "Admin"})
    user = admin or await user_collection.find_one({})
    uid = str(user["_id"])
    headers = {"X-User-Id": uid}

    cam = await camera_collection.find_one({"ip_address": {"$regex": "^192\\.168\\.41"}})
    if not cam:
        cam = await camera_collection.find_one({})
    cam_uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "")
    stream = stream_name(cam_uid, "sub")
    print(f"Test camera: {cam.get('name')} ip={cam.get('ip_address')} uid={cam_uid} stream={stream}")

    base = "http://127.0.0.1:10000"
    async with aiohttp.ClientSession() as s:
        for path in ["/api/live/config", "/api/go2rtc/status"]:
            async with s.get(base + path, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
                body = (await r.text())[:200]
                print(f"{path}: {r.status} {body}")

        async with s.get(
            f"{base}/go2rtc/video-stream.js",
            headers={**headers, "Accept": "text/javascript,*/*"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            body = await r.text()
            print(f"video-stream.js: {r.status} len={len(body)}")

        async with s.get(
            f"{base}/go2rtc/api/streams",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            print(f"go2rtc streams API (proxied): {r.status}")

        # Direct go2rtc
        async with s.get(
            f"{GO2RTC_API_URL}/api/streams",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()
            names = list((data or {}).keys())
            print(f"go2rtc direct streams count: {len(names)}")
            print(f"  test stream registered: {stream in names}")

        # Pull one JPEG frame (starts RTSP on demand — confirms camera is reachable)
        async with s.get(
            f"{GO2RTC_API_URL}/api/frame.jpeg?src={stream}",
            timeout=aiohttp.ClientTimeout(total=25),
        ) as r:
            body = await r.read()
            ct = r.headers.get("Content-Type", "")
            ok = r.status == 200 and len(body) > 500 and "image" in ct
            print(
                f"go2rtc frame {stream}: {r.status} "
                f"bytes={len(body)} type={ct} "
                f"{'OK — stream live' if ok else 'FAIL'}"
            )


asyncio.run(main())
