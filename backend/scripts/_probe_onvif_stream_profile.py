#!/usr/bin/env python3
"""Live ONVIF stream profile probe — read only unless --apply-test is passed."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402
from app.services.stream_profile_service import (  # noqa: E402
    apply_camera_stream_profile,
    get_camera_stream_profile,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("camera_id", help="Mongo camera _id")
    parser.add_argument("--apply-test-fps", type=int, help="Temporarily set sub fps, then restore")
    args = parser.parse_args()

    before = await get_camera_stream_profile(args.camera_id)
    print("=== BEFORE ===")
    print(json.dumps(before, indent=2, default=str))

    if args.apply_test_fps is not None:
        sub = before.get("sub") or {}
        cur_fps = (sub.get("current") or {}).get("fps")
        if not sub.get("supported"):
            print("Sub stream not supported — skip apply test")
            return
        test_fps = args.apply_test_fps
        print(f"\n=== APPLY sub fps {test_fps} ===")
        applied = await apply_camera_stream_profile(
            args.camera_id,
            {"sub": {"fps": test_fps}},
        )
        print(json.dumps(applied, indent=2, default=str))
        after = await get_camera_stream_profile(args.camera_id)
        print("\n=== AFTER APPLY ===")
        print(json.dumps(after, indent=2, default=str))
        if cur_fps is not None:
            print(f"\n=== RESTORE sub fps {cur_fps} ===")
            restored = await apply_camera_stream_profile(
                args.camera_id,
                {"sub": {"fps": int(round(cur_fps))}},
            )
            print(json.dumps(restored, indent=2, default=str))
            final = await get_camera_stream_profile(args.camera_id)
            print("\n=== FINAL ===")
            print(json.dumps(final, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
