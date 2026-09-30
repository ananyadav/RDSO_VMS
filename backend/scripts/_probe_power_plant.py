"""Probe all rml_6_isp_power_plant cameras: TCP554 + go2rtc JPEG."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection
from app.services.go2rtc_service import stream_name
from app.services.go2rtc_workers import get_api_url_for_camera_doc, normalize_worker_id


async def ping_tcp(ip: str, timeout: float = 2.5) -> bool:
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, 554), timeout=timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def frame_bytes(session: aiohttp.ClientSession, base: str, stream: str) -> int:
    url = f"{base.rstrip('/')}/api/frame.jpeg?src={stream}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200:
                return -resp.status
            return len(await resp.read())
    except Exception:
        return -1


async def main() -> None:
    cams = [
        c
        async for c in camera_collection.find({"location_path": "rml_6_isp_power_plant"})
    ]
    print(f"power_plant_cameras={len(cams)}")
    async with aiohttp.ClientSession() as session:
        ok = fail_tcp = fail_frame = 0
        codecs = {}
        for c in cams:
            ip = str(c.get("ip_address") or "")
            uid = c.get("camera_uid") or ""
            wid = normalize_worker_id(c.get("worker_id")) or 1
            proto = c.get("protocol") or "?"
            base = await get_api_url_for_camera_doc(c)
            sn = stream_name(uid, "sub")
            tcp = await ping_tcp(ip) if ip else False
            nbytes = await frame_bytes(session, base, sn) if base else -1
            # codec from streams info (best effort)
            codec = "?"
            try:
                async with session.get(
                    f"{base.rstrip('/')}/api/streams",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json() if resp.status == 200 else {}
                info = data.get(sn) or {}
                for p in info.get("producers") or []:
                    for m in p.get("medias") or []:
                        if "H265" in m or "HEVC" in m.upper():
                            codec = "H265"
                        elif "H264" in m and codec == "?":
                            codec = "H264"
                    for rcv in p.get("receivers") or []:
                        cn = ((rcv.get("codec") or {}).get("codec_name") or "").lower()
                        if cn in ("hevc", "h265"):
                            codec = "H265"
                        elif cn in ("h264",) and codec == "?":
                            codec = "H264"
            except Exception:
                pass
            codecs[codec] = codecs.get(codec, 0) + 1
            status = "OK" if tcp and nbytes > 1000 else ("NO_TCP" if not tcp else f"NO_FRAME({nbytes})")
            if status == "OK":
                ok += 1
            elif not tcp:
                fail_tcp += 1
            else:
                fail_frame += 1
            print(
                f"{ip:16} w{wid} {proto:10} codec={codec:4} tcp={tcp} frame={nbytes:7} {status}"
            )
        print("\nSUMMARY")
        print(f"  streaming_ok={ok}  tcp_fail={fail_tcp}  frame_fail={fail_frame}")
        print(f"  codecs={codecs}")


if __name__ == "__main__":
    asyncio.run(main())
