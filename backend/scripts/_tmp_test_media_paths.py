#!/usr/bin/env python3
"""Test Nginx direct-media path (frame + WebSocket) with admin auth."""
import asyncio
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import user_collection  # noqa: E402


async def main() -> None:
    admin = await user_collection.find_one({"role": "Admin"}) or await user_collection.find_one({})
    headers = {"X-User-Id": str(admin["_id"])}
    stream = "ip_192_168_11_25_sub"
    frame_url = f"http://127.0.0.1:8080/media/w3/api/frame.jpeg?src={stream}"
    ws_url = f"ws://127.0.0.1:8080/media/w3/api/ws?src={stream}"

    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(frame_url, timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.read()
            print(f"8080 frame: {r.status} bytes={len(body)} type={r.headers.get('Content-Type', '')}")

        try:
            async with s.ws_connect(ws_url, timeout=15) as ws:
                print("8080 websocket: connected")
                msg = await asyncio.wait_for(ws.receive(), timeout=10)
                print(f"8080 websocket: first message type={msg.type}")
        except Exception as exc:
            print(f"8080 websocket: FAIL {exc}")

    frame3000 = f"http://127.0.0.1:3000/media/w3/api/frame.jpeg?src={stream}"
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(frame3000, timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.read()
            print(f"3000 frame: {r.status} bytes={len(body)} type={r.headers.get('Content-Type', '')}")


if __name__ == "__main__":
    asyncio.run(main())
