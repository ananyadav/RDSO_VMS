#!/usr/bin/env python3
"""
RDSO 18.3.5 — opt-in acceptance: minimum 128 simultaneous recording streams.

SAFETY:
  Requires RDSO_128_ACCEPTANCE=1
  Does NOT modify production schedule/master/.env
  Writes only to a temporary acceptance directory, then deletes it
  Intended for the Linux VMS server with adequate disk — NOT low-storage laptops

Usage (on the Linux VMS server, project root):

  RDSO_128_ACCEPTANCE=1 \\
    python backend/scripts/rdso_18_3_5_accept_128_streams.py \\
      --duration 45 --count 128 --segment-seconds 2

Dry selection check (still requires the flag):

  RDSO_128_ACCEPTANCE=1 python backend/scripts/rdso_18_3_5_accept_128_streams.py --dry-select
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description="RDSO 18.3.5 — 128 simultaneous recording acceptance")
    parser.add_argument("--count", type=int, default=128, help="Target simultaneous streams (default 128)")
    parser.add_argument("--duration", type=int, default=45, help="Hold seconds while all streams active")
    parser.add_argument("--segment-seconds", type=int, default=2, help="Short HLS segments for acceptance only")
    parser.add_argument("--stagger-ms", type=int, default=25, help="Delay between FFmpeg starts")
    parser.add_argument("--stream", choices=("main", "sub"), default="main")
    parser.add_argument(
        "--health-url",
        default=os.getenv("RDSO_ACCEPTANCE_HEALTH_URL", "http://127.0.0.1:10000/api/health"),
    )
    parser.add_argument(
        "--dry-select",
        action="store_true",
        help="Only select cameras and print counts (still requires RDSO_128_ACCEPTANCE=1)",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default="",
        help="Optional path to write the JSON report",
    )
    args = parser.parse_args()

    from app.services.rdso_128_acceptance import (
        acceptance_flag_enabled,
        require_acceptance_flag,
        run_128_stream_acceptance,
        select_acceptance_cameras,
    )
    from app.services.recording_config import get_recording_capacity_info

    if not acceptance_flag_enabled():
        print(
            "REFUSED: set RDSO_128_ACCEPTANCE=1 to run this acceptance tool.",
            file=sys.stderr,
        )
        return 2

    require_acceptance_flag()
    cap = get_recording_capacity_info()
    print("capacity:", json.dumps(cap, indent=2), flush=True)
    if not cap.get("rdso_18_3_5_software_compliant", True):
        print(
            "WARNING: RECORDING_MAX_CONCURRENT soft-cap is below RDSO minimum 128.",
            file=sys.stderr,
        )

    if args.dry_select:
        async def _select():
            slots = await select_acceptance_cameras(count=args.count, prefer_stream=args.stream)
            print(f"selected={len(slots)} requested={args.count}")
            for s in slots[:10]:
                print(f"  {s.ip_address} {s.camera_uid} worker={s.worker_id}")
            if len(slots) > 10:
                print(f"  ... +{len(slots) - 10} more")
            return 0 if len(slots) >= args.count else 1

        return asyncio.run(_select())

    report = asyncio.run(
        run_128_stream_acceptance(
            stream_count=args.count,
            duration_seconds=args.duration,
            segment_seconds=args.segment_seconds,
            stagger_ms=args.stagger_ms,
            prefer_stream=args.stream,
            health_url=args.health_url,
        )
    )
    payload = report.to_dict()
    text = json.dumps(payload, indent=2)
    print(text, flush=True)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")

    print("RESULT:", "PASS" if report.passed else "FAIL", flush=True)
    if report.fail_reasons:
        for reason in report.fail_reasons:
            print(" -", reason, flush=True)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
