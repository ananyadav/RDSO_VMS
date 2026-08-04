"""Sequential RTSP probe — frame.jpeg + TCP 554 per camera."""
import asyncio
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
from app.services.go2rtc_workers import get_api_url_for_camera_doc
from app.services.stream_issues import classify_stream_error


async def tcp_open(ip: str, port: int = 554, timeout: float = 3) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        await w.wait_closed()
        return True
    except Exception:
        return False


async def probe_frame(session: aiohttp.ClientSession, cam: dict) -> dict:
    ip = (cam.get("ip_address") or "").strip()
    uid = cam.get("camera_uid") or make_camera_uid(ip) or ""
    stream = stream_name(uid, "sub")
    base = await get_api_url_for_camera_doc(cam)
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}&timeout=15"

    reachable = await tcp_open(ip)
    if not reachable:
        return {"ip": ip, "tcp": False, "ok": False, "category": "timeout", "detail": "RTSP port 554 closed/unreachable"}

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            body = await resp.read()
            if resp.status == 200 and len(body) > 1000:
                return {"ip": ip, "tcp": True, "ok": True, "category": "online", "detail": f"{len(body)} bytes"}
            text = body.decode("utf-8", errors="replace")[:200]
            cat = classify_stream_error(text or f"HTTP {resp.status}")
            return {"ip": ip, "tcp": True, "ok": False, "category": cat, "detail": text or f"HTTP {resp.status} ({len(body)}b)"}
    except asyncio.TimeoutError:
        return {"ip": ip, "tcp": True, "ok": False, "category": "timeout", "detail": "frame.jpeg timeout"}
    except Exception as exc:
        return {"ip": ip, "tcp": True, "ok": False, "category": classify_stream_error(str(exc)), "detail": str(exc)[:120]}


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    active_q = {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
    cameras = await camera_collection.find(active_q).sort("ip_address", 1).limit(limit).to_list(None)

    counts: dict[str, int] = {}
    failures: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for cam in cameras:
            result = await probe_frame(session, cam)
            cat = result["category"]
            counts[cat] = counts.get(cat, 0) + 1
            if not result["ok"]:
                failures.append(result)
            print(
                f"{'OK' if result['ok'] else 'FAIL':4} {result['ip']:16} "
                f"tcp={result.get('tcp')} {cat}: {result.get('detail','')}"
            )

    print("\n=== summary ===")
    total = len(cameras)
    ok = counts.get("online", 0)
    print(f"probed: {total}, streaming OK: {ok}, failed: {total - ok}")
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        if cat != "online":
            print(f"  {cat}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
