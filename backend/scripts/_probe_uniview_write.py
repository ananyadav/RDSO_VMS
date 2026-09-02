#!/usr/bin/env python3
"""Apply/restore ONVIF sub FPS on non-ONVIF-labelled camera; verify isolation."""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.services.stream_profile_service import (  # noqa: E402
    apply_camera_stream_profile,
    get_camera_stream_profile,
)

TARGET = "6a577948608cd73e5f441cae"  # UNIVIEW 192.168.7.20
OTHER = "6a577948608cd73e5f441cbb"  # DAHUA 192.168.7.75


async def main() -> None:
    before = await get_camera_stream_profile(TARGET)
    sub_fps = ((before.get("sub") or {}).get("current") or {}).get("fps")
    print("TARGET before sub fps:", sub_fps)
    other_before = await get_camera_stream_profile(OTHER)
    other_sub = ((other_before.get("sub") or {}).get("current") or {}).get("fps")
    print("OTHER before sub fps:", other_sub)

    if not (before.get("sub") or {}).get("supported"):
        print("Target sub not supported — abort")
        return

    test_fps = 12 if sub_fps != 12 else 13
    applied = await apply_camera_stream_profile(TARGET, {"sub": {"fps": test_fps}})
    print("APPLY:", json.dumps(applied, indent=2))
    after = await get_camera_stream_profile(TARGET)
    print("TARGET after sub fps:", ((after.get("sub") or {}).get("current") or {}).get("fps"))

    other_after = await get_camera_stream_profile(OTHER)
    print("OTHER after sub fps:", ((other_after.get("sub") or {}).get("current") or {}).get("fps"))

    if sub_fps is not None:
        restored = await apply_camera_stream_profile(TARGET, {"sub": {"fps": int(round(sub_fps))}})
        print("RESTORE:", json.dumps(restored, indent=2))
        final = await get_camera_stream_profile(TARGET)
        print("TARGET final sub fps:", ((final.get("sub") or {}).get("current") or {}).get("fps"))


if __name__ == "__main__":
    asyncio.run(main())
