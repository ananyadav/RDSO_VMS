"""
Phase 1 pilot: record 2 cameras (substream, 4-day default, low disk).

Usage (from project root, backend must be running):
  python backend/scripts/start_test_recording.py
  python backend/scripts/start_test_recording.py --hours 24
  python backend/scripts/start_test_recording.py --camera-id <id1> --camera-id <id2>
  Default cameras: Cam10 + Cam8 (or set PILOT_CAMERA_NAMES in .env)

Status:
  python backend/scripts/start_test_recording.py --status
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def api(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1: 4-day pilot recording on 2 cameras")
    parser.add_argument("--api-url", default="http://127.0.0.1:10000")
    parser.add_argument("--hours", type=float, default=96)
    parser.add_argument("--camera-id", action="append", dest="camera_ids", default=[])
    parser.add_argument("--status", action="store_true", help="Show pilot status only")
    args = parser.parse_args()
    base = args.api_url.rstrip("/")

    try:
        if args.status:
            st = api("GET", f"{base}/api/recordings/pilot/status")
            print(json.dumps(st, indent=2))
            return 0

        body: dict = {"hours": args.hours}
        if args.camera_ids:
            body["cameraIds"] = args.camera_ids
        else:
            # Cam10 + Cam8 (testing) — matches PILOT_CAMERA_NAMES default
            body["cameraIds"] = [
                "6a16cbc2138cc87cda0f77b2",  # Cam10
                "6a16cbc2138cc87cda0f77b0",  # Cam8
            ]

        result = api("POST", f"{base}/api/recordings/pilot/start", body)
        print("Phase 1 pilot recording started")
        print(f"  Cameras: {', '.join(result.get('camera_names', []))}")
        print(f"  IDs:     {result.get('camera_ids', [])}")
        print(f"  Until:   {result.get('ends_at')}")
        print(f"  Stream:  {result.get('stream_profile')}")
        print(f"  Storage: {result.get('storage_root')}/<camera_id>/sessions/<session_id>/")
        print(f"  Quality: {result.get('quality_note', '')}")
        for err in result.get("errors", []):
            print(f"  [WARN] {err}")
        return 0 if not result.get("errors") else 1

    except urllib.error.URLError as e:
        print(f"Cannot reach backend at {base}: {e}")
        print("Start: python -m backend.app.main --api-port 10000")
        return 1
    except urllib.error.HTTPError as e:
        print(e.read().decode())
        return 1


if __name__ == "__main__":
    sys.exit(main())
