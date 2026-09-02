#!/usr/bin/env python3
"""
RDSO 18.1.5 — verify one camera can record, serve live (go2rtc), and playback
archived segments simultaneously. Restores schedule/recording state on exit.

Usage (project root):
  python backend/scripts/validate_rdso_18_1_5_simultaneous.py
  python backend/scripts/validate_rdso_18_1_5_simultaneous.py --camera-ip 192.168.41.106
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Engine + short segments for controlled local test only (does not change fleet schedule).
os.environ.setdefault("RECORDING_ENABLED", "true")
os.environ.setdefault("RECORDING_HLS_SEGMENT_SECONDS", "10")
os.environ.setdefault("RECORDING_VIA_GO2RTC", "true")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
# Re-apply after .env load so test overrides win when unset in .env
os.environ["RECORDING_ENABLED"] = "true"
os.environ["RECORDING_HLS_SEGMENT_SECONDS"] = "10"

DEFAULT_TEST_IP = "192.168.41.106"
GO2RTC_BASE = os.getenv("GO2RTC_LOCAL_URL", "http://127.0.0.1:1984").rstrip("/")


def _log(msg: str) -> None:
    print(msg, flush=True)


def _ffmpeg_processes_for_uid(uid: str) -> list[str]:
    import subprocess

    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='ffmpeg.exe'", "get", "commandline"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        out = ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip() and ln.strip().lower() != "commandline"]
    return [ln for ln in lines if uid in ln or "rtsp" in ln.lower()]


def _go2rtc_live_ok(stream_src: str, timeout: float = 8.0) -> tuple[bool, str]:
    url = f"{GO2RTC_BASE}/api/frame.jpeg?src={stream_src}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256)
            ok = resp.status == 200 and len(body) > 100
            return ok, f"HTTP {resp.status}, {len(body)} bytes from {url}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} for {url}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def _load_camera_by_ip(ip: str) -> dict:
    from app.core.database import camera_collection

    doc = await camera_collection.find_one({"ip_address": ip})
    if not doc:
        raise SystemExit(f"Camera IP {ip} not found in MongoDB")
    return doc


async def _save_schedule_snapshot() -> dict:
    from app.services import recording_schedule_store as sched

    await sched.bootstrap_recording_schedule()
    return {
        "master_enabled": sched.master_enabled,
        "schedule": dict(sched.recording_schedule),
    }


async def _restore_schedule_snapshot(snapshot: dict) -> None:
    from app.services import recording_schedule_store as sched
    from app.services.video_recording import cleanup_all_recordings, is_camera_recording, stop_camera_recording

    for cid, on in snapshot["schedule"].items():
        if on and await is_camera_recording(cid):
            await stop_camera_recording(cid)
    await cleanup_all_recordings()

    sched.master_enabled = snapshot["master_enabled"]
    sched.recording_schedule = dict(snapshot["schedule"])
    await sched.save_recording_settings()


async def run_test(camera_ip: str, wait_seconds: int) -> dict:
    from app.services import recording_schedule_store as sched
    from app.services.recording_config import is_recording_engine_enabled
    from app.services.recording_media import build_recording_media_response
    from app.services.playback_search import search_recordings_by_date
    from app.services.video_recording import (
        ACTIVE_RECORDINGS,
        is_camera_recording,
        start_camera_recording,
        stop_camera_recording,
    )

    if not is_recording_engine_enabled():
        raise SystemExit("RECORDING_ENABLED is false after bootstrap")

    camera = await _load_camera_by_ip(camera_ip)
    camera_id = str(camera["_id"])
    uid = camera.get("camera_uid") or f"ip_{camera_ip.replace('.', '_')}"
    sub_stream = f"{uid}_sub"
    main_stream = f"{uid}_main"

    snapshot = await _save_schedule_snapshot()
    result: dict = {
        "camera_ip": camera_ip,
        "camera_id": camera_id,
        "camera_uid": uid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "recording_engine_enabled": True,
        "baseline_master_enabled": snapshot["master_enabled"],
        "baseline_camera_scheduled": snapshot["schedule"].get(camera_id, False),
    }

    try:
        # Schedule only this camera ON (master follows via _sync_master_with_schedule).
        sched.set_camera_recording(camera_id, True)
        await sched.save_recording_settings()
        enabled_count = sum(1 for v in sched.recording_schedule.values() if v)
        result["cameras_scheduled_on"] = enabled_count
        if enabled_count != 1:
            _log(f"[WARN] Expected exactly 1 camera ON, got {enabled_count}")

        session = await start_camera_recording(camera_id)
        session_id = session["id"]
        result["session_id"] = session_id
        result["recording_started"] = True

        session_dir = ROOT / "Recordings" / uid / "sessions" / session_id
        deadline = time.monotonic() + wait_seconds
        segments_before = 0
        while time.monotonic() < deadline:
            segs = list(session_dir.glob("seg_*.ts"))
            if segs:
                segments_before = len(segs)
                break
            await asyncio.sleep(1)
        result["first_segment_wait_s"] = round(wait_seconds - (deadline - time.monotonic()), 1)
        result["segments_after_start"] = segments_before
        if segments_before == 0:
            result["recording_segments_ok"] = False
            raise RuntimeError(f"No segments created within {wait_seconds}s under {session_dir}")

        live_ok, live_detail = _go2rtc_live_ok(sub_stream)
        result["live_go2rtc_stream"] = sub_stream
        result["live_ok"] = live_ok
        result["live_detail"] = live_detail

        # Playback while recording: read playlist + first segment via recording media layer.
        playlist_resp = await build_recording_media_response(uid, session_id, "index.m3u8")
        playlist_text = playlist_resp.text or ""
        result["playback_playlist_ok"] = "#EXTM3U" in playlist_text
        result["playback_playlist_live"] = "#EXT-X-ENDLIST" not in playlist_text

        seg_files = sorted(session_dir.glob("seg_*.ts"))
        if seg_files:
            await build_recording_media_response(uid, session_id, seg_files[0].name)
            seg_len = seg_files[0].stat().st_size
            result["playback_segment_ok"] = seg_len > 0
            result["playback_segment_bytes"] = seg_len

        ffmpeg_lines = _ffmpeg_processes_for_uid(uid)
        result["ffmpeg_process_count"] = len(ffmpeg_lines)
        result["ffmpeg_duplicate"] = len(ffmpeg_lines) > 1
        result["recording_active_in_memory"] = await is_camera_recording(camera_id)

        await asyncio.sleep(12)
        segments_after = len(list(session_dir.glob("seg_*.ts")))
        result["segments_after_wait"] = segments_after
        result["recording_continued_during_playback"] = segments_after >= segments_before

        live_ok_2, _ = _go2rtc_live_ok(sub_stream)
        result["live_still_ok_after_playback"] = live_ok_2

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        search = await search_recordings_by_date(uid, today)
        playable = [r for r in (search.get("recordings") or []) if r.get("sessionId") == session_id]
        result["playback_search_playable"] = bool(playable and playable[0].get("playable"))

        result["success"] = all(
            [
                result.get("recording_started"),
                result.get("recording_segments_ok", segments_before > 0),
                result.get("live_ok"),
                result.get("playback_playlist_ok"),
                result.get("playback_segment_ok"),
                result.get("recording_continued_during_playback"),
                result.get("live_still_ok_after_playback"),
                not result.get("ffmpeg_duplicate"),
                camera_id in ACTIVE_RECORDINGS,
            ]
        )
        return result
    finally:
        try:
            if await is_camera_recording(camera_id):
                await stop_camera_recording(camera_id)
        except Exception as exc:
            _log(f"[WARN] stop recording: {exc}")
        await _restore_schedule_snapshot(snapshot)
        result["restored"] = True


async def main() -> None:
    parser = argparse.ArgumentParser(description="RDSO 18.1.5 simultaneous record/live/playback test")
    parser.add_argument("--camera-ip", default=DEFAULT_TEST_IP)
    parser.add_argument("--wait-seconds", type=int, default=45, help="Max wait for first HLS segment")
    args = parser.parse_args()

    _log("RDSO 18.1.5 simultaneous validation")
    _log(f"  Test camera IP: {args.camera_ip}")
    _log(f"  RECORDING_ENABLED=true (process env only)")
    _log(f"  Segment length: {os.environ.get('RECORDING_HLS_SEGMENT_SECONDS')}s")

    result = await run_test(args.camera_ip, args.wait_seconds)
    _log("\n=== Results ===")
    _log(json.dumps(result, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
