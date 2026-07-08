#!/usr/bin/env python3
"""Quick worker 1 vs worker 2 live frame probe (low concurrency)."""
import asyncio
import socket
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")


async def probe_worker(wid: int) -> None:
    from app.core.database import camera_collection
    from app.services.camera_uid import make_camera_uid
    from app.services.go2rtc_service import stream_name
    from app.services.go2rtc_workers import worker_base_url, normalize_worker_id

    base = worker_base_url(wid)
    cams = []
    async for doc in camera_collection.find({"is_active": {"$ne": False}}):
        if normalize_worker_id(doc.get("worker_id")) != wid:
            continue
        ip = doc.get("ip_address") or ""
        uid = doc.get("camera_uid") or make_camera_uid(ip)
        cams.append((doc.get("name") or ip, ip, stream_name(uid, "sub")))

    ok = fail_net = fail_rtsp = 0
    fails = []
    sem = asyncio.Semaphore(3)

    async with aiohttp.ClientSession() as s:

        async def one(name, ip, stream):
            nonlocal ok, fail_net, fail_rtsp
            try:
                socket.create_connection((ip, 554), timeout=2.5).close()
                tcp = True
            except OSError:
                tcp = False
            if not tcp:
                fail_net += 1
                fails.append((name, ip, "network"))
                return
            async with sem:
                try:
                    async with s.get(
                        f"{base}/api/frame.jpeg?src={stream}",
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as r:
                        b = await r.read()
                        if r.status == 200 and len(b) > 500:
                            ok += 1
                        else:
                            fail_rtsp += 1
                            fails.append((name, ip, f"rtsp b={len(b)}"))
                except Exception as exc:
                    fail_rtsp += 1
                    fails.append((name, ip, str(exc)[:40]))

        await asyncio.gather(*[one(*c) for c in cams])

    print(f"Worker {wid}: total={len(cams)} ok={ok} network={fail_net} rtsp={fail_rtsp}")
    for row in fails[:10]:
        print(f"  {row[0][:30]:30} {row[1]:15} {row[2]}")


async def main() -> None:
    await probe_worker(1)
    print()
    await probe_worker(2)


if __name__ == "__main__":
    asyncio.run(main())
