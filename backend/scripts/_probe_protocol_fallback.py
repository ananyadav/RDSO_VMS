#!/usr/bin/env python3
"""Probe stream profile read across protocols (ONVIF fallback for non-ISAPI)."""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402
from app.services.stream_profile_service import get_camera_stream_profile  # noqa: E402


async def probe_protocol(protocol: str) -> None:
    cam = await camera_collection.find_one(
        {"protocol": protocol},
        {"name": 1, "ip_address": 1, "protocol": 1},
    )
    if not cam:
        print(f"\n=== {protocol}: no camera in DB ===")
        return
    cid = str(cam["_id"])
    print(f"\n=== {protocol}: {cam.get('name')} {cam.get('ip_address')} ({cid}) ===")
    result = await get_camera_stream_profile(cid)
    summary = {
        "supported": result.get("supported"),
        "driver": result.get("driver"),
        "message": result.get("message"),
        "main": {
            "supported": (result.get("main") or {}).get("supported"),
            "fps": ((result.get("main") or {}).get("current") or {}).get("fps"),
            "resolution": ((result.get("main") or {}).get("current") or {}).get("resolution"),
            "message": (result.get("main") or {}).get("message"),
        },
        "sub": {
            "supported": (result.get("sub") or {}).get("supported"),
            "fps": ((result.get("sub") or {}).get("current") or {}).get("fps"),
            "resolution": ((result.get("sub") or {}).get("current") or {}).get("resolution"),
            "message": (result.get("sub") or {}).get("message"),
        },
    }
    print(json.dumps(summary, indent=2))


async def main() -> None:
    for protocol in ("UNIVIEW", "DAHUA", "CUSTOM", "VIVOTEK", "ONVIF", "HIKVISION"):
        await probe_protocol(protocol)


if __name__ == "__main__":
    asyncio.run(main())
