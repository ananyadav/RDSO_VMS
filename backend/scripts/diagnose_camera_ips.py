#!/usr/bin/env python3
"""Check worker assignment and go2rtc reachability for specific camera IPs."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

IPS = [
    "192.168.13.14",
    "192.168.14.26",
    "192.168.14.35",
    "192.168.14.43",
    "192.168.14.52",
    "192.168.14.55",
    "192.168.46.26",
    "192.168.46.32",
    "192.168.46.9",
    "192.168.7.21",
    "192.168.7.22",
    "192.168.7.3",
    "192.168.7.4",
    "192.168.7.5",
    "192.168.7.6",
    "192.168.7.75",
    "192.168.7.87",
]


def mask_rtsp(url: str) -> str:
    return re.sub(r":([^:@/]+)@", ":***@", url or "")


async def main() -> None:
    from app.core.database import camera_collection
    from app.services.camera_uid import make_camera_uid
    from app.services.go2rtc_service import fetch_go2rtc_streams, stream_name
    from app.services.go2rtc_workers import WORKERS_ENABLED, list_active_workers, worker_base_url

    workers = await list_active_workers()
    print(f"WORKERS_ENABLED={WORKERS_ENABLED} workers={[int(w['worker_id']) for w in workers]}")
    worker_health: dict[int, int] = {}
    async with aiohttp.ClientSession() as session:
        for w in workers:
            wid = int(w["worker_id"])
            url = worker_base_url(wid)
            try:
                async with session.get(
                    f"{url}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json() if resp.status == 200 else {}
                    n = len(data) if isinstance(data, dict) else 0
                    worker_health[wid] = n
                    print(f"  worker {wid} {url} OK streams={n}")
            except Exception as exc:
                worker_health[wid] = -1
                print(f"  worker {wid} {url} DOWN ({exc})")

    print()
    by_worker: dict[str, list[str]] = {}
    for ip in IPS:
        cam = await camera_collection.find_one({"$or": [{"ip_address": ip}, {"ip": ip}]})
        if not cam:
            print(f"{ip}: NOT IN DATABASE")
            continue
        uid = cam.get("camera_uid") or make_camera_uid(ip) or "?"
        wid = str(cam.get("worker_id", "?"))
        by_worker.setdefault(wid, []).append(ip)
        sub_key = stream_name(uid, "sub")
        active = cam.get("is_active", True)
        sub_url = mask_rtsp(cam.get("sub_rtsp_url") or "")
        print(
            f"{ip}: worker={wid} active={active} stream={sub_key} "
            f"worker_streams={worker_health.get(int(wid) if wid.isdigit() else 0, '?')} "
            f"rtsp={sub_url[:90]}"
        )

    print("\nBy worker:")
    for wid, ips in sorted(by_worker.items(), key=lambda x: x[0]):
        print(f"  worker {wid}: {len(ips)} sample IPs — {', '.join(ips[:5])}{'…' if len(ips) > 5 else ''}")


if __name__ == "__main__":
    asyncio.run(main())
