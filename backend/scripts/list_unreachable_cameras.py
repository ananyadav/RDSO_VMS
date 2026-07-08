#!/usr/bin/env python3
import asyncio
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")


def tcp_ok(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 554), timeout=2.5):
            return True
    except OSError:
        return False


async def main() -> None:
    from app.core.database import camera_collection
    from app.services.go2rtc_workers import normalize_worker_id

    bad = []
    async for doc in camera_collection.find({"is_active": {"$ne": False}}):
        ip = (doc.get("ip_address") or "").strip()
        if not ip or tcp_ok(ip):
            continue
        bad.append(
            {
                "ip": ip,
                "name": doc.get("name") or ip,
                "worker": normalize_worker_id(doc.get("worker_id")) or 1,
                "site": doc.get("site") or "",
                "building": doc.get("building") or "",
            }
        )
    bad.sort(key=lambda x: x["ip"])
    print(f"Unreachable (TCP 554): {len(bad)}\n")
    for c in bad:
        loc = " / ".join(x for x in (c["site"], c["building"]) if x) or "—"
        print(f"{c['ip']}\tworker {c['worker']}\t{c['name']}\t{loc}")


if __name__ == "__main__":
    asyncio.run(main())
