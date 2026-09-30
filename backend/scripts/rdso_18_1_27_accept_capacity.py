#!/usr/bin/env python3
"""RDSO 18.1.27 — safe opt-in capacity acceptance (does NOT open 128 streams locally).

Software checks only by default. For Linux 128-stream soak, set RDSO_128_ACCEPTANCE=1
and run backend/scripts/rdso_18_3_5_accept_128_streams.py (shared harness).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="RDSO 18.1.27 software capacity probe")
    parser.add_argument(
        "--run-128-streams",
        action="store_true",
        help="Delegate to 18.3.5 harness (requires RDSO_128_ACCEPTANCE=1; Linux only)",
    )
    args = parser.parse_args()

    from app.services.rdso_18_1_27_capacity import get_rdso_18_1_27_capacity

    cap = get_rdso_18_1_27_capacity()
    print(json.dumps(cap, indent=2))
    if not cap.get("software_compliant"):
        print("FAIL: software capacity below RDSO 18.1.27 minima", file=sys.stderr)
        return 1

    print("[OK] Software capacity evidence meets 18.1.27 minima")
    print("NOTE: Physical 8-monitor + Linux 128-stream soak still required for site acceptance.")

    if args.run_128_streams:
        print(
            "To run the 128-stream soak on Linux:\n"
            "  set RDSO_128_ACCEPTANCE=1\n"
            "  python backend/scripts/rdso_18_3_5_accept_128_streams.py --count 128\n"
            "This script intentionally does not open streams on developer machines."
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
