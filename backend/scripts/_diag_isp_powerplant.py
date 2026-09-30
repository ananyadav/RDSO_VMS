"""Diagnose ISP / Power Plant cameras."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402


async def ping_tcp(ip: str, port: int = 554, timeout: float = 3.0) -> tuple[bool, str]:
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


async def main() -> None:
    q = {
        "$or": [
            {"name": {"$regex": "power.?plant|isp", "$options": "i"}},
            {"display_name": {"$regex": "power.?plant|isp", "$options": "i"}},
            {"site": {"$regex": "power.?plant|isp", "$options": "i"}},
            {"building": {"$regex": "power.?plant|isp", "$options": "i"}},
            {"location_path": {"$regex": "power.?plant|isp", "$options": "i"}},
            {"camera_group": {"$regex": "power.?plant|isp", "$options": "i"}},
        ]
    }
    cams = [c async for c in camera_collection.find(q)]
    print(f"matched={len(cams)}")
    print("sites", Counter(str(c.get("site") or "") for c in cams))
    print("buildings", Counter(str(c.get("building") or "") for c in cams))
    print("location_path samples", Counter(str(c.get("location_path") or "")[:80] for c in cams).most_common(15))

    # Prefer cameras that look like ISP + Power Plant specifically
    isp = []
    for c in cams:
        blob = " ".join(
            str(c.get(k) or "")
            for k in ("name", "display_name", "site", "building", "location_path", "camera_group")
        ).lower()
        if "isp" in blob and ("power" in blob or "plant" in blob):
            isp.append(c)
        elif "power plant" in blob or "powerplant" in blob:
            isp.append(c)
    if not isp:
        # fall back to building ISP
        isp = [c for c in cams if str(c.get("building") or "").upper() == "ISP"]
    if not isp:
        isp = cams[:30]

    print(f"\nfocus_count={len(isp)}")
    sample = isp[:25]
    print("\n--- sample cameras ---")
    for c in sample:
        print(
            json.dumps(
                {
                    "name": c.get("name") or c.get("display_name"),
                    "ip": c.get("ip_address"),
                    "active": c.get("is_active", True),
                    "online": c.get("online", c.get("is_online")),
                    "site": c.get("site"),
                    "building": c.get("building"),
                    "path": c.get("location_path"),
                    "worker": c.get("worker_id"),
                    "uid": c.get("camera_uid"),
                    "protocol": c.get("protocol"),
                },
                default=str,
            )
        )

    ips = [str(c.get("ip_address") or "").strip() for c in sample if c.get("ip_address")]
    ips = list(dict.fromkeys(ips))[:15]
    print("\n--- TCP 554 reachability (sample) ---")
    results = await asyncio.gather(*(ping_tcp(ip) for ip in ips))
    for ip, (ok, reason) in zip(ips, results):
        print(f"{ip}: {'OK' if ok else 'FAIL'} ({reason})")


if __name__ == "__main__":
    asyncio.run(main())
