"""Quick authenticated smoke test for VMS backend APIs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

load_dotenv(ROOT / ".env")

BASE = os.getenv("VITE_API_BASE_URL", "http://127.0.0.1:10000").rstrip("/")


async def _admin_user_id() -> str:
    from app.core.database import user_collection

    doc = await user_collection.find_one({"role": "Admin"})
    if not doc:
        doc = await user_collection.find_one({})
    if not doc:
        raise RuntimeError("No users in database — cannot run authenticated checks")
    return str(doc["_id"])


async def _get(session: aiohttp.ClientSession, path: str, uid: str) -> tuple[int, object]:
    url = f"{BASE}{path}"
    async with session.get(url, headers={"X-User-Id": uid}) as resp:
        text = await resp.text()
        try:
            body = json.loads(text) if text else {}
        except json.JSONDecodeError:
            body = text[:200]
        return resp.status, body


def _ok(label: str, status: int, body: object, issues: list[str]) -> None:
    if status == 200:
        print(f"  OK  {label} ({status})")
    else:
        msg = f"  FAIL {label} HTTP {status}"
        if isinstance(body, dict) and body.get("error"):
            msg += f" — {body['error']}"
        print(msg)
        issues.append(label)


async def main() -> int:
    issues: list[str] = []
    print(f"VMS health check -> {BASE}\n")

    try:
        uid = await _admin_user_id()
        print(f"Using admin user id: {uid[:8]}...\n")
    except Exception as exc:
        print(f"FAIL database: {exc}")
        return 1

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        checks = [
            ("/api/cameras", "Cameras list"),
            ("/api/locations", "Locations"),
            ("/api/recordings/schedule", "Recording schedule"),
            ("/api/recordings/health", "Recording health"),
            ("/api/storage/dashboard?summary=1", "Storage dashboard (summary)"),
            ("/api/go2rtc/diagnostics", "go2rtc diagnostics"),
        ]

        for path, label in checks:
            try:
                status, body = await _get(session, path, uid)
                _ok(label, status, body, issues)
                if label == "Storage dashboard (summary)" and status == 200 and isinstance(body, dict):
                    s = body.get("summary") or {}
                    print(
                        f"       recordings={s.get('recordings_storage_gb', 0)} GB, "
                        f"segments={s.get('total_segments', 0)}, "
                        f"recording={s.get('cameras_recording', 0)} cams"
                    )
                if label == "go2rtc diagnostics" and status == 200 and isinstance(body, dict):
                    print(
                        f"       provider={body.get('liveProvider')}, "
                        f"streams={len(body.get('streams') or [])}, "
                        f"missing={len(body.get('missingInGo2rtc') or body.get('missingStreams') or [])}"
                    )
                if label == "Recording health" and status == 200 and isinstance(body, dict):
                    cams = body.get("cameras") or []
                    rec = sum(1 for c in cams if c.get("is_recording"))
                    err = sum(1 for c in cams if c.get("recording_status") not in (None, "recording", "idle", "scheduled"))
                    print(f"       {len(cams)} cameras, {rec} recording, {err} with errors")
            except Exception as exc:
                print(f"  FAIL {label} — {exc}")
                issues.append(label)

        # Full storage scan (slower)
        try:
            print("\n  ... full storage scan (may take a minute)")
            status, body = await _get(session, "/api/storage/dashboard", uid)
            _ok("Storage dashboard (full)", status, body, issues)
            if status == 200 and isinstance(body, dict):
                s = body.get("summary") or {}
                print(
                    f"       recordings={s.get('recordings_storage_gb', 0)} GB, "
                    f"segments={s.get('total_segments', 0)}"
                )
        except Exception as exc:
            print(f"  FAIL Storage dashboard (full) — {exc}")
            issues.append("Storage full scan")

    print()
    if issues:
        print(f"RESULT: {len(issues)} issue(s) — {', '.join(issues)}")
        return 1
    print("RESULT: All checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
