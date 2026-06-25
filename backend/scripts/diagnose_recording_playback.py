#!/usr/bin/env python3
"""
Check recording + playback readiness on the NVR server.

Usage (from project root):
  python backend/scripts/diagnose_recording_playback.py
  python backend/scripts/diagnose_recording_playback.py --camera Cam10 --date 2026-06-23
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def check_ffmpeg() -> bool:
    print("\n=== FFmpeg ===")
    from app.services.ffmpeg_util import ffmpeg_bin

    path = ffmpeg_bin()
    if not path:
        _fail("ffmpeg not found — install ffmpeg or set FFMPEG_PATH in .env")
        return False
    if path == "ffmpeg" and not shutil.which("ffmpeg"):
        _fail("ffmpeg not on PATH and FFMPEG_PATH invalid")
        return False
    env = os.getenv("FFMPEG_PATH") or os.getenv("FFMPEG_BIN") or ""
    if env and ("\\" in env or env.lower().endswith(".exe")) and os.name != "nt":
        _fail(f"FFMPEG_PATH looks like a Windows path on Linux: {env}")
        return False
    _ok(f"ffmpeg: {path}")
    return True


async def check_recordings_dir() -> Path | None:
    print("\n=== Recordings folder ===")
    from app.services.storage_settings_store import load_storage_settings, get_effective_recordings_dir

    await load_storage_settings()
    rec_dir = get_effective_recordings_dir()
    if not rec_dir.is_dir():
        _fail(f"Directory missing: {rec_dir}")
        return None
    _ok(f"Path: {rec_dir}")
    test = rec_dir / ".write_test"
    try:
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        _ok("Writable")
    except OSError as exc:
        _fail(f"Not writable: {exc}")
        return None

    session_dirs = list(rec_dir.glob("*/sessions/*"))
    playlists = [p for p in rec_dir.glob("*/sessions/*/index.m3u8") if p.is_file()]
    segments = list(rec_dir.glob("*/sessions/*/*.ts"))
    print(f"  Camera folders: {len({p.parent.parent.parent.name for p in session_dirs})}")
    print(f"  Sessions with index.m3u8: {len(playlists)}")
    print(f"  .ts segment files: {len(segments)}")
    if not playlists:
        _warn("No recorded sessions on disk yet - recording may not have run")
    else:
        latest = max(playlists, key=lambda p: p.stat().st_mtime)
        mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
        _ok(f"Latest playlist: {latest.relative_to(rec_dir)} ({mtime.isoformat()})")
    return rec_dir


async def check_schedule_and_health() -> None:
    print("\n=== Recording schedule ===")
    from app.services import recording_schedule_store as sched
    from app.services.recording_health import get_recording_health

    await sched.bootstrap_recording_schedule()
    enabled = sum(1 for v in sched.recording_schedule.values() if v)
    total = len(sched.recording_schedule)
    print(f"  Master recording: {'ON' if sched.master_enabled else 'OFF'}")
    print(f"  Cameras scheduled: {enabled}/{total}")
    if not sched.master_enabled:
        _warn("Master recording is OFF - enable in Storage > Recording tab")
    elif enabled == 0:
        _warn("No cameras enabled in schedule - turn on floors/cameras in schedule")

    scheduled_ids = {cid for cid, on in sched.recording_schedule.items() if on}
    health = await get_recording_health(scheduled_ids)
    summary = health.get("summary", {})
    print(f"  Health: {summary.get('healthy', 0)} healthy, "
          f"{summary.get('recording', 0)} recording, "
          f"{summary.get('offline', 0)} offline, "
          f"{summary.get('idle', 0)} idle")
    for cam in health.get("cameras", [])[:8]:
        print(
            f"    - {cam.get('camera_name')}: {cam.get('health_label')} "
            f"(rec={cam.get('recording_status')}, ffmpeg={cam.get('ffmpeg_status')})"
        )
    if len(health.get("cameras", [])) > 8:
        print(f"    ... and {len(health['cameras']) - 8} more")


async def check_playback(camera_ref: str | None, date: str | None) -> None:
    print("\n=== Playback search ===")
    from app.core.database import camera_collection
    from app.services.playback_search import search_recordings_by_date

    if not camera_ref:
        doc = await camera_collection.find_one({"is_active": {"$ne": False}})
        if not doc:
            _warn("No cameras in DB — skip playback test")
            return
        from app.services.camera_identity import make_camera_uid

        uid = doc.get("camera_uid") or make_camera_uid(doc.get("ip_address", ""))
        camera_ref = uid or str(doc["_id"])
        print(f"  Using first camera ref: {camera_ref} ({doc.get('name')})")

    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"  Date: {date}")

    try:
        result = await search_recordings_by_date(camera_ref, date)
    except Exception as exc:
        _fail(f"Search error: {exc}")
        return

    recordings = result.get("recordings") or []
    print(f"  Sessions found: {len(recordings)}")
    playable = [r for r in recordings if r.get("playable") and (r.get("segmentCount") or 0) > 0]
    if playable:
        r = playable[0]
        _ok(f"Sample playable session: {r.get('sessionId')} segments={r.get('segmentCount')}")
        _ok(f"Playlist URL: {r.get('playlistUrl')}")
    elif recordings:
        _warn("Sessions in DB/metadata but not playable (missing files on disk?)")
        for r in recordings[:3]:
            print(f"    - {r.get('sessionId')}: playable={r.get('playable')} err={r.get('error')}")
    else:
        _warn("No recordings for this date - pick a date when recording was active")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose NVR recording and playback")
    parser.add_argument("--camera", help="camera_uid or Mongo id for playback test")
    parser.add_argument("--date", help="YYYY-MM-DD for playback test")
    args = parser.parse_args()

    print("NVR recording / playback diagnostics")
    print(f"  Python: {sys.version.split()[0]}  OS: {os.name}")
    print(f"  MONGODB_URI set: {bool(os.getenv('MONGODB_URI'))}")

    ok_ffmpeg = check_ffmpeg()
    await check_recordings_dir()
    await check_schedule_and_health()
    await check_playback(args.camera, args.date)

    print("\n=== Quick fixes (server deploy) ===")
    print("  1. .env: use Linux paths - FFMPEG_PATH=/usr/bin, RECORDINGS_DIR=/var/nvr/recordings")
    print("  2. Remove Windows GO2RTC_BIN=.exe - use Linux go2rtc binary or GO2RTC_ENABLED=false")
    print("  3. Storage > Recording: Recording Active ON + schedule cameras/floors ON")
    print("  4. MongoDB Atlas: whitelist server public IP")
    print("  5. Server must reach cameras on RTSP port (usually 554)")
    print("  6. Production: ./start_production.sh (UI+API same port) - not dev VITE_API_BASE_URL=127.0.0.1")
    if not ok_ffmpeg:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
