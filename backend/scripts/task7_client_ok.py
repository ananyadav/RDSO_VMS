#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection, user_collection  # noqa: E402
from app.services.session_service import SESSION_COOKIE_NAME, create_session  # noqa: E402

IPS = [
    "192.168.41.106",
    "192.168.41.13",
    "192.168.41.23",
    "192.168.41.24",
    "192.168.41.41",
]


async def main() -> None:
    user = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not user:
        user = await user_collection.find_one({})
    token = await create_session(str(user["_id"]))
    cookies = {SESSION_COOKIE_NAME: token}
    async with aiohttp.ClientSession(cookies=cookies) as s:
        for ip in IPS:
            cam = await camera_collection.find_one({"ip_address": ip})
            uid = cam.get("camera_uid")
            body = {"cameraId": str(cam["_id"]), "cameraUid": uid, "stream": f"{uid}_sub"}
            async with s.post("http://127.0.0.1:10000/api/go2rtc/client-ok", json=body) as r:
                print(ip, "client-ok", r.status, (await r.text())[:120])
        async with s.get(
            "http://127.0.0.1:10000/api/cameras?group=rml_6_corporate_office_2nd_floor"
        ) as r:
            cams = await r.json()
        for c in cams:
            if c.get("name") in IPS:
                print(
                    "list",
                    c["name"],
                    "online",
                    c.get("online"),
                    c.get("liveStatus"),
                    "confirmedOffline",
                    c.get("confirmedOffline"),
                )


if __name__ == "__main__":
    asyncio.run(main())
