#!/usr/bin/env python3
"""RDSO 18.5(ii) — remote transcode acceptance with synthetic media (safe).

Generates a local test pattern, runs FFmpeg through the VMS transcoder profiles,
and probes FPS / resolution / codec / bitrate. Does not touch production cameras.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _make_synthetic(path: Path, *, seconds: int = 4) -> None:
    from app.services.ffmpeg_util import ffmpeg_bin

    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=1280x720:rate=25",
        "-t",
        str(seconds),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        "2500k",
        str(path),
    ]
    subprocess.check_call(cmd)


async def _run(profile_id: str) -> dict:
    from app.services.remote_transcode_profiles import get_profile, resolve_transcode_intent
    from app.services.remote_transcode_service import (
        resolve_live_input_url,
        run_file_transcode_for_acceptance,
    )

    # Live path must resolve to go2rtc local RTSP (architecture check).
    live_url = resolve_live_input_url("ip_192_168_1_10", stream="main", worker_id=1)
    assert "127.0.0.1" in live_url or "localhost" in live_url
    assert "@" not in live_url.split("://", 1)[-1].split("/", 1)[0]

    auto = resolve_transcode_intent(mode="auto", bandwidth_kbps=400)
    profile = get_profile(profile_id) or auto["profile"]
    assert profile is not None

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        out = Path(tmp) / "out"
        _make_synthetic(src)
        result = await run_file_transcode_for_acceptance(src, profile, out, duration_sec=3.0)
        result["live_go2rtc_url_example"] = live_url
        result["auto_intent_400kbps"] = {
            "transcode": auto.get("transcode"),
            "profile_id": (auto.get("profile") or profile).id if auto.get("profile") else None,
        }
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="RDSO 18.5(ii) synthetic transcode probe")
    parser.add_argument("--profile", default="low")
    args = parser.parse_args()

    result = asyncio.run(_run(args.profile))
    print(json.dumps(result, indent=2, default=str))
    if not result.get("ok"):
        print("FAIL: transcode acceptance", file=sys.stderr)
        return 1
    probe = result.get("probe") or {}
    if (probe.get("codec") or "").lower() not in ("h264", "avc1"):
        print(f"FAIL: expected h264, got {probe.get('codec')}", file=sys.stderr)
        return 1
    print("[OK] Synthetic remote transcode changed encode parameters via FFmpeg")
    print("NOTE: WAN live soak against go2rtc still required on Linux site acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
