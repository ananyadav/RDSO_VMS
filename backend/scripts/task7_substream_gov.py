#!/usr/bin/env python3
"""Apply Task 7 substream I-frame (~1s) via in-place XML replace."""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402
from app.services.hikvision_ptz import _isapi  # noqa: E402

TEST_IPS = [
    "192.168.41.106",
    "192.168.41.13",
    "192.168.41.23",
    "192.168.41.24",
    "192.168.41.41",
]


def parse_fields(xml: str) -> dict:
    out = {}
    for tag in (
        "videoCodecType",
        "videoResolutionWidth",
        "videoResolutionHeight",
        "maxFrameRate",
        "GovLength",
        "keyFrameInterval",
        "constantBitRate",
        "vbrUpperCap",
    ):
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
        out[tag] = m.group(1) if m else None
    return out


async def get_put(ip: str, gov: int | None, key_ms: int | None) -> None:
    cam = await camera_collection.find_one({"ip_address": ip})
    if not cam:
        print(f"{ip}: missing")
        return
    st101, xml101_b = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    st102, xml102_b = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    print(f"\n{ip} BEFORE 102 {parse_fields(xml102_b)}")
    print(f"{ip} BEFORE 101 {parse_fields(xml101_b)}")
    if gov is None:
        return
    new = re.sub(r"(<GovLength>)(\d+)(</GovLength>)", rf"\g<1>{gov}\g<3>", xml102_b, count=1)
    new = re.sub(
        r"(<keyFrameInterval>)(\d+)(</keyFrameInterval>)",
        rf"\g<1>{key_ms}\g<3>",
        new,
        count=1,
    )
    pst, ptxt = await _isapi(cam, "PUT", "/ISAPI/Streaming/channels/102", body=new.encode("utf-8"))
    print(f"{ip} PUT {pst} {ptxt[:80].replace(chr(10), ' ')}")
    _, xml102_a = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    _, xml101_a = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    print(f"{ip} AFTER  102 {parse_fields(xml102_a)}")
    print(f"{ip} AFTER  101 {parse_fields(xml101_a)}")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["inspect", "set1s", "revert"])
    p.add_argument("--ips", nargs="*", default=TEST_IPS)
    args = p.parse_args()
    if args.mode == "inspect":
        for ip in args.ips:
            await get_put(ip, None, None)
    elif args.mode == "set1s":
        for ip in args.ips:
            # 15 fps => GovLength 15 frames; keyFrameInterval 1000 ms
            await get_put(ip, 15, 1000)
    else:
        for ip in args.ips:
            await get_put(ip, 50, 3333)


if __name__ == "__main__":
    asyncio.run(main())
