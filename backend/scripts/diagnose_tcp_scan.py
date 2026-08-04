"""Fast TCP 554 scan for all active cameras."""
import asyncio
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection


async def tcp_open(ip: str, port: int = 554, timeout: float = 2.5) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        await w.wait_closed()
        return True
    except Exception:
        return False


async def main() -> None:
    active_q = {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
    cameras = await camera_collection.find(active_q, {"ip_address": 1, "site": 1, "building": 1}).to_list(None)
    sem = asyncio.Semaphore(40)

    async def check(cam: dict) -> tuple[str, bool, str, str]:
        ip = (cam.get("ip_address") or "").strip()
        async with sem:
            ok = await tcp_open(ip) if ip else False
        return ip, ok, cam.get("site") or "", cam.get("building") or ""

    results = await asyncio.gather(*[check(c) for c in cameras])
    reachable = [r for r in results if r[1]]
    unreachable = [r for r in results if not r[1]]

    print(f"=== TCP 554 scan ({len(cameras)} cameras) ===")
    print(f"  reachable: {len(reachable)}")
    print(f"  unreachable: {len(unreachable)}")

    by_site = Counter(site for _, _, site, _ in unreachable)
    if by_site:
        print("\n  unreachable by site:")
        for site, n in by_site.most_common(10):
            print(f"    {site}: {n}")

    print("\n  sample unreachable IPs:")
    for ip, _, site, building in unreachable[:25]:
        print(f"    {ip} ({site} / {building})")
    if len(unreachable) > 25:
        print(f"    ... and {len(unreachable) - 25} more")


if __name__ == "__main__":
    asyncio.run(main())
