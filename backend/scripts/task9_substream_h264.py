#!/usr/bin/env python3
"""Task 9: inspect / switch channel 102 H.265 → H.264 on 5 test cameras only."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

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

# Target after codec change (same as Task 7 H.265 profile except codec).
TARGET = {
    "videoCodecType": None,  # filled from capabilities
    "videoResolutionWidth": "640",
    "videoResolutionHeight": "360",
    "maxFrameRate": "1500",  # Hikvision: 100 = 1 fps → 1500 = 15 fps
    "GovLength": "15",
    "keyFrameInterval": "1000",
    "constantBitRate": "320",
}

FFPROBE = (
    Path(r"C:\Users\Ananya Yadav\Downloads")
    / "ffmpeg-2026-05-25-git-34dfa8bf2b-essentials_build"
    / "bin"
    / "ffprobe.exe"
)

TAGS = (
    "videoCodecType",
    "videoResolutionWidth",
    "videoResolutionHeight",
    "maxFrameRate",
    "GovLength",
    "keyFrameInterval",
    "constantBitRate",
    "vbrUpperCap",
    "videoQualityControlType",
)


def parse_fields(xml: str) -> dict:
    out = {}
    for tag in TAGS:
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
        out[tag] = m.group(1) if m else None
    return out


def replace_tag(xml: str, tag: str, value: str) -> str:
    pattern = rf"(<{tag}>)([^<]*)(</{tag}>)"
    if not re.search(pattern, xml):
        raise ValueError(f"tag <{tag}> not found in XML")
    return re.sub(pattern, rf"\g<1>{value}\g<3>", xml, count=1)


def pick_h264_codec(cap_xml: str, current: str) -> str:
    """Pick an H.264 codec token from StreamingChannelCap / VideoCap enumerations."""
    # Common Hikvision tokens
    candidates = []
    for m in re.finditer(r'opt="([^"]+)"', cap_xml):
        candidates.append(m.group(1))
    for m in re.finditer(r"<videoCodecType[^>]*>([^<]+)</videoCodecType>", cap_xml):
        candidates.append(m.group(1))
    # Flatten comma-separated option lists
    tokens: list[str] = []
    for c in candidates:
        for part in re.split(r"[,|]", c):
            part = part.strip()
            if part:
                tokens.append(part)
    # Prefer exact H.264 labels
    preferred = ["H.264", "H264", "h.264", "h264"]
    for p in preferred:
        if p in tokens:
            return p
    for t in tokens:
        if "264" in t and "265" not in t and "HEVC" not in t.upper():
            return t
    # Fall back if capabilities omit enum but camera currently reports H.265
    if current and "265" in current:
        return "H.264"
    raise RuntimeError(f"No H.264 codec found in capabilities; tokens={sorted(set(tokens))[:40]}")


async def inspect_one(ip: str) -> dict:
    cam = await camera_collection.find_one({"ip_address": ip})
    if not cam:
        return {"ip": ip, "error": "missing"}
    st101, xml101 = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    st102, xml102 = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    stcap, cap = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102/capabilities")
    fields102 = parse_fields(xml102)
    h264 = None
    try:
        h264 = pick_h264_codec(cap or "", fields102.get("videoCodecType") or "")
    except Exception as e:  # noqa: BLE001
        h264 = f"ERR:{e}"
    return {
        "ip": ip,
        "id": str(cam.get("_id")),
        "cameraUid": cam.get("camera_uid") or cam.get("cameraUid"),
        "ch101_status": st101,
        "ch101": parse_fields(xml101),
        "ch102_status": st102,
        "ch102": fields102,
        "cap_status": stcap,
        "suggested_h264": h264,
        "cap_codec_snippet": (cap or "")[:500],
    }


async def set_h264(ip: str) -> dict:
    cam = await camera_collection.find_one({"ip_address": ip})
    if not cam:
        return {"ip": ip, "error": "missing"}

    _, xml101_before = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    _, xml102_before = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    _, cap = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102/capabilities")
    before102 = parse_fields(xml102_before)
    before101 = parse_fields(xml101_before)
    h264 = pick_h264_codec(cap or "", before102.get("videoCodecType") or "")

    # Step 1: change codec only (in-place XML)
    step1 = replace_tag(xml102_before, "videoCodecType", h264)
    pst1, ptxt1 = await _isapi(cam, "PUT", "/ISAPI/Streaming/channels/102", body=step1.encode("utf-8"))
    _, xml_mid = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    mid = parse_fields(xml_mid)

    # Step 2: restore resolution / fps / bitrate / GOP if camera drifted
    # Keep Task 7 profile except codec. Preserve VBR if that was the prior mode.
    restores = {
        "videoCodecType": h264,
        "videoResolutionWidth": TARGET["videoResolutionWidth"],
        "videoResolutionHeight": TARGET["videoResolutionHeight"],
        "maxFrameRate": TARGET["maxFrameRate"],
        "GovLength": TARGET["GovLength"],
        "keyFrameInterval": TARGET["keyFrameInterval"],
        "constantBitRate": TARGET["constantBitRate"],
    }
    if re.search(r"<vbrUpperCap>", xml_mid):
        restores["vbrUpperCap"] = TARGET["constantBitRate"]
    if before102.get("videoQualityControlType"):
        restores["videoQualityControlType"] = before102["videoQualityControlType"]

    fixed = xml_mid
    for tag, val in restores.items():
        if re.search(rf"<{tag}>", fixed):
            fixed = replace_tag(fixed, tag, val)

    pst2, ptxt2 = await _isapi(cam, "PUT", "/ISAPI/Streaming/channels/102", body=fixed.encode("utf-8"))
    _, xml_after = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    _, xml101_after = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    after102 = parse_fields(xml_after)
    after101 = parse_fields(xml101_after)

    return {
        "ip": ip,
        "h264_token": h264,
        "put1": {"status": pst1, "body": (ptxt1 or "")[:120]},
        "put2": {"status": pst2, "body": (ptxt2 or "")[:120]},
        "before102": before102,
        "mid102": mid,
        "after102": after102,
        "before101": before101,
        "after101": after101,
        "ch101_unchanged": before101 == after101,
    }


def ffprobe_stream(ip: str, user: str, password: str) -> dict:
    url = f"rtsp://{user}:{quote(password, safe='')}@{ip}:554/Streaming/Channels/102"
    # Stream info
    cmd_info = [
        str(FFPROBE),
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        url,
    ]
    # Keyframe gaps (~6s)
    cmd_kf = [
        str(FFPROBE),
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
        "%+6",
        url,
    ]
    info = subprocess.run(cmd_info, capture_output=True, text=True, timeout=40)
    kf = subprocess.run(cmd_kf, capture_output=True, text=True, timeout=50)
    out: dict = {"ip": ip}
    if info.returncode == 0:
        streams = (json.loads(info.stdout or "{}").get("streams") or [{}])[0]
        out["codec_name"] = streams.get("codec_name")
        out["width"] = streams.get("width")
        out["height"] = streams.get("height")
        out["avg_frame_rate"] = streams.get("avg_frame_rate")
        out["r_frame_rate"] = streams.get("r_frame_rate")
    else:
        out["info_error"] = (info.stderr or "")[-300:]
    if kf.returncode == 0:
        frames = json.loads(kf.stdout or "{}").get("frames") or []
        keys = []
        for f in frames:
            if str(f.get("key_frame")) == "1" or f.get("pict_type") == "I":
                try:
                    keys.append(float(f.get("pkt_pts_time")))
                except (TypeError, ValueError):
                    pass
        gaps = [round(keys[i + 1] - keys[i], 3) for i in range(len(keys) - 1)]
        out["key_gaps_sec"] = gaps[:8]
        out["gap_median"] = sorted(gaps)[len(gaps) // 2] if gaps else None
    else:
        out["kf_error"] = (kf.stderr or "")[-300:]
    return out


async def probe_all(ips: list[str]) -> list[dict]:
    results = []
    for ip in ips:
        cam = await camera_collection.find_one({"ip_address": ip})
        if not cam:
            results.append({"ip": ip, "error": "missing"})
            continue
        results.append(
            ffprobe_stream(ip, cam.get("username") or "admin", str(cam.get("password") or ""))
        )
    return results


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["inspect", "set-h264", "ffprobe"])
    p.add_argument("--ips", nargs="*", default=TEST_IPS)
    args = p.parse_args()
    if args.mode == "inspect":
        rows = []
        for ip in args.ips:
            rows.append(await inspect_one(ip))
        print(json.dumps(rows, indent=2))
    elif args.mode == "set-h264":
        rows = []
        for ip in args.ips:
            rows.append(await set_h264(ip))
        print(json.dumps(rows, indent=2))
    else:
        print(json.dumps(await probe_all(args.ips), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
