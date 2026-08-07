#!/usr/bin/env python3
"""Measure actual IDR/keyframe spacing on channel 102 via ffprobe (Task 7 evidence)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402

FFPROBE = r"C:\Users\Ananya Yadav\Downloads\ffmpeg-2026-05-25-git-34dfa8bf2b-essentials_build\bin\ffprobe.exe"
IPS = [
    "192.168.41.106",
    "192.168.41.13",
    "192.168.41.23",
    "192.168.41.24",
    "192.168.41.41",
]


async def measure(ip: str) -> None:
    cam = await camera_collection.find_one({"ip_address": ip})
    user = cam.get("username") or "admin"
    password = quote(str(cam.get("password") or ""), safe="")
    url = f"rtsp://{user}:{password}@{ip}:554/Streaming/Channels/102"
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=key_frame,pkt_pts_time,pict_type",
        "-of",
        "json",
        "-read_intervals",
        "%+8",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if proc.returncode != 0:
        print(f"{ip}: ffprobe failed: {proc.stderr[-300:]}")
        return
    data = json.loads(proc.stdout or "{}")
    frames = data.get("frames") or []
    keys = []
    for f in frames:
        if str(f.get("key_frame")) == "1" or f.get("pict_type") == "I":
            try:
                keys.append(float(f.get("pkt_pts_time")))
            except (TypeError, ValueError):
                pass
    gaps = [round(keys[i + 1] - keys[i], 3) for i in range(len(keys) - 1)]
    print(
        json.dumps(
            {
                "ip": ip,
                "frames_seen": len(frames),
                "keyframes": len(keys),
                "key_pts": keys[:8],
                "gaps_sec": gaps[:8],
                "gap_median": sorted(gaps)[len(gaps) // 2] if gaps else None,
            }
        )
    )


async def main() -> None:
    for ip in IPS:
        await measure(ip)


if __name__ == "__main__":
    asyncio.run(main())
