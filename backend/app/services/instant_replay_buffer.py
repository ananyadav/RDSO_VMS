"""Bounded Instant Replay rolling HLS buffer (one producer per camera).

Started only when Live View / Instant Replay acquires a lease — not fleet-wide.
Uses go2rtc local RTSP (copy/remux). Temporary media under Recordings/_instant_replay/.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.ffmpeg_util import ffmpeg_bin
from app.services.instant_replay_config import (
    INSTANT_REPLAY_BUFFER_SECONDS,
    INSTANT_REPLAY_ENABLED,
    INSTANT_REPLAY_IDLE_GRACE_SECONDS,
    INSTANT_REPLAY_LEASE_TTL_SECONDS,
    INSTANT_REPLAY_SEGMENT_SECONDS,
)
from app.services.storage_settings_store import get_effective_recordings_dir

logger = logging.getLogger(__name__)

FFMPEG = ffmpeg_bin()
IR_FOLDER = "_instant_replay"

# camera_uid -> buffer state
_BUFFERS: Dict[str, Dict[str, Any]] = {}
_LOCKS: Dict[str, asyncio.Lock] = {}
_reaper_task: Optional[asyncio.Task] = None


def _lock_for(uid: str) -> asyncio.Lock:
    if uid not in _LOCKS:
        _LOCKS[uid] = asyncio.Lock()
    return _LOCKS[uid]


def instant_replay_root() -> Path:
    root = get_effective_recordings_dir() / IR_FOLDER
    root.mkdir(parents=True, exist_ok=True)
    return root


def buffer_dir(camera_uid: str) -> Path:
    path = instant_replay_root() / camera_uid
    path.mkdir(parents=True, exist_ok=True)
    return path


def buffer_playlist_url(camera_uid: str) -> str:
    return f"/api/playback/instant-replay-buffer/{camera_uid}/media/index.m3u8"


def _rtsp_timeout_args() -> list:
    flag = "-timeout" if os.name == "nt" else "-stimeout"
    args = [flag, "5000000"]
    if os.name != "nt":
        args.extend(["-rw_timeout", "5000000"])
    return args


def _list_size() -> int:
    return max(2, int(INSTANT_REPLAY_BUFFER_SECONDS / max(1, INSTANT_REPLAY_SEGMENT_SECONDS)) + 2)


def _segment_bounds(session_dir: Path) -> tuple[Optional[datetime], Optional[datetime], int]:
    if not session_dir.is_dir():
        return None, None, 0
    segs = sorted(session_dir.glob("seg_*.ts"))
    if not segs:
        segs = sorted(session_dir.glob("*.ts"))
    usable = [s for s in segs if s.is_file() and s.stat().st_size > 0]
    if not usable:
        return None, None, 0
    mtimes = [datetime.fromtimestamp(s.stat().st_mtime, tz=timezone.utc) for s in usable]
    return min(mtimes), max(mtimes), len(usable)


async def _start_ffmpeg(camera_uid: str, camera_doc: dict) -> Dict[str, Any]:
    from app.services.camera_uid import make_camera_uid
    from app.services.go2rtc_service import local_recording_rtsp_url
    from app.services.go2rtc_workers import WORKERS_ENABLED, normalize_worker_id

    uid = (
        (camera_doc.get("camera_uid") or make_camera_uid(camera_doc.get("ip_address") or "") or camera_uid)
        .strip()
    )
    worker_id = None
    if WORKERS_ENABLED:
        worker_id = normalize_worker_id(camera_doc.get("worker_id")) or 1
    # Substream for IR buffer — lighter than main evidence recording.
    rtsp = local_recording_rtsp_url(uid, "sub", worker_id=worker_id)

    out_dir = buffer_dir(uid)
    # Fresh playlist each start
    for old in out_dir.glob("*"):
        try:
            if old.is_file():
                old.unlink()
        except OSError:
            pass

    playlist = out_dir / "index.m3u8"
    pattern = str(out_dir / "seg_%05d.ts")
    list_size = str(_list_size())
    hls_flags = "delete_segments+append_list+program_date_time+independent_segments"

    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-probesize",
        "512000",
        "-analyzeduration",
        "500000",
        "-rtsp_transport",
        "tcp",
        *_rtsp_timeout_args(),
        "-i",
        rtsp,
        "-an",
        "-c:v",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(INSTANT_REPLAY_SEGMENT_SECONDS),
        "-hls_list_size",
        list_size,
        "-hls_flags",
        hls_flags,
        "-hls_segment_filename",
        pattern,
        str(playlist),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
    )
    logger.info(
        "[IR-BUFFER] Started camera=%s pid=%s dir=%s segment=%ss window=%ss",
        uid,
        proc.pid,
        out_dir,
        INSTANT_REPLAY_SEGMENT_SECONDS,
        INSTANT_REPLAY_BUFFER_SECONDS,
    )
    return {
        "camera_uid": uid,
        "process": proc,
        "dir": out_dir,
        "started_at": datetime.now(timezone.utc),
        "leases": {},  # lease_id -> last_heartbeat monotonic
        "idle_since": None,
        "stderr_task": asyncio.create_task(_drain_stderr(uid, proc)),
    }


async def _drain_stderr(uid: str, proc: asyncio.subprocess.Process) -> None:
    if not proc.stderr:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            msg = line.decode("utf-8", errors="ignore").strip()
            if not msg:
                continue
            lower = msg.lower()
            if "error" in lower or "failed" in lower:
                logger.error("[IR-BUFFER][ffmpeg][%s] %s", uid, msg)
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def _stop_buffer_unlocked(uid: str, *, reason: str) -> None:
    state = _BUFFERS.pop(uid, None)
    if not state:
        return
    task = state.get("stderr_task")
    if task:
        task.cancel()
    proc = state.get("process")
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception as exc:
            logger.warning("[IR-BUFFER] Stop error camera=%s: %s", uid, exc)
    out_dir = state.get("dir")
    if out_dir and Path(out_dir).is_dir():
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass
    logger.info("[IR-BUFFER] Stopped camera=%s reason=%s", uid, reason)


async def acquire_buffer_lease(camera_uid: str, camera_doc: dict, lease_id: str | None = None) -> dict:
    """Acquire/refresh a consumer lease; start one buffer producer if needed."""
    if not INSTANT_REPLAY_ENABLED:
        return {"ok": False, "error": "Instant Replay buffer disabled", "enabled": False}

    uid = (camera_uid or "").strip()
    if not uid:
        return {"ok": False, "error": "cameraUid required"}

    lid = (lease_id or "").strip() or str(uuid.uuid4())
    async with _lock_for(uid):
        state = _BUFFERS.get(uid)
        if state:
            proc = state.get("process")
            if proc and proc.returncode is not None:
                logger.warning("[IR-BUFFER] Dead process camera=%s — restarting", uid)
                await _stop_buffer_unlocked(uid, reason="dead_process")
                state = None

        if state is None:
            try:
                state = await _start_ffmpeg(uid, camera_doc)
                _BUFFERS[uid] = state
            except Exception as exc:
                logger.error("[IR-BUFFER] Start failed camera=%s: %s", uid, exc, exc_info=True)
                return {"ok": False, "error": f"Failed to start replay buffer: {exc}"}

        state["leases"][lid] = time.monotonic()
        state["idle_since"] = None
        ensure_reaper_running()

        oldest, newest, seg_count = _segment_bounds(state["dir"])
        return {
            "ok": True,
            "leaseId": lid,
            "cameraUid": uid,
            "playlistUrl": buffer_playlist_url(uid),
            "sourceType": "instant_replay_buffer",
            "oldestAvailableAt": oldest.isoformat() if oldest else None,
            "newestAvailableAt": newest.isoformat() if newest else None,
            "segmentCount": seg_count,
            "bufferSeconds": INSTANT_REPLAY_BUFFER_SECONDS,
            "segmentSeconds": INSTANT_REPLAY_SEGMENT_SECONDS,
        }


async def heartbeat_buffer_lease(camera_uid: str, lease_id: str) -> dict:
    uid = (camera_uid or "").strip()
    lid = (lease_id or "").strip()
    state = _BUFFERS.get(uid)
    if not state or lid not in state.get("leases", {}):
        return {"ok": False, "error": "Unknown lease"}
    state["leases"][lid] = time.monotonic()
    state["idle_since"] = None
    oldest, newest, seg_count = _segment_bounds(state["dir"])
    return {
        "ok": True,
        "leaseId": lid,
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "segmentCount": seg_count,
    }


async def release_buffer_lease(camera_uid: str, lease_id: str) -> dict:
    uid = (camera_uid or "").strip()
    lid = (lease_id or "").strip()
    async with _lock_for(uid):
        state = _BUFFERS.get(uid)
        if not state:
            return {"ok": True, "released": False}
        state["leases"].pop(lid, None)
        if not state["leases"]:
            state["idle_since"] = time.monotonic()
        return {"ok": True, "released": True, "remainingLeases": len(state["leases"])}


def get_buffer_window(camera_uid: str) -> dict:
    state = _BUFFERS.get((camera_uid or "").strip())
    if not state:
        return {
            "active": False,
            "oldestAvailableAt": None,
            "newestAvailableAt": None,
            "segmentCount": 0,
        }
    oldest, newest, seg_count = _segment_bounds(state["dir"])
    return {
        "active": True,
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "segmentCount": seg_count,
        "playlistUrl": buffer_playlist_url(camera_uid),
        "sourceType": "instant_replay_buffer",
    }


def resolve_buffer_offset(camera_uid: str, target: datetime) -> Optional[dict]:
    """Map target time into buffer playlist offset if covered."""
    from datetime import timedelta

    state = _BUFFERS.get((camera_uid or "").strip())
    if not state:
        return None
    oldest, newest, _ = _segment_bounds(state["dir"])
    if oldest is None or newest is None:
        return None
    if target < oldest:
        return None
    if target > newest + timedelta(seconds=INSTANT_REPLAY_SEGMENT_SECONDS):
        target = newest
    offset = max(0.0, (target - oldest).total_seconds())
    duration = max(0.0, (newest - oldest).total_seconds())
    offset = min(offset, duration)
    return {
        "ok": True,
        "sourceType": "instant_replay_buffer",
        "sessionId": None,
        "playlistUrl": buffer_playlist_url(camera_uid),
        "offsetSeconds": round(offset, 3),
        "oldestAvailableAt": oldest.isoformat(),
        "newestAvailableAt": newest.isoformat(),
        "status": "buffering",
    }


def buffer_process_count_for_tests() -> int:
    return len(_BUFFERS)


def lease_count_for_camera(camera_uid: str) -> int:
    state = _BUFFERS.get((camera_uid or "").strip())
    if not state:
        return 0
    return len(state.get("leases") or {})


async def _reaper_loop() -> None:
    while True:
        try:
            await asyncio.sleep(5)
            now = time.monotonic()
            ttl = float(INSTANT_REPLAY_LEASE_TTL_SECONDS)
            grace = float(INSTANT_REPLAY_IDLE_GRACE_SECONDS)
            for uid in list(_BUFFERS.keys()):
                async with _lock_for(uid):
                    state = _BUFFERS.get(uid)
                    if not state:
                        continue
                    # Expire stale leases
                    stale = [
                        lid
                        for lid, ts in list(state["leases"].items())
                        if now - ts > ttl
                    ]
                    for lid in stale:
                        state["leases"].pop(lid, None)
                    if not state["leases"]:
                        if state.get("idle_since") is None:
                            state["idle_since"] = now
                        elif now - state["idle_since"] >= grace:
                            await _stop_buffer_unlocked(uid, reason="idle_grace")
                    else:
                        state["idle_since"] = None
                    # Dead process cleanup
                    state = _BUFFERS.get(uid)
                    if state:
                        proc = state.get("process")
                        if proc and proc.returncode is not None:
                            await _stop_buffer_unlocked(uid, reason="process_exited")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("[IR-BUFFER] Reaper error: %s", exc)


def ensure_reaper_running() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())


async def cleanup_all_instant_replay_buffers(*, reason: str = "shutdown") -> None:
    global _reaper_task
    if _reaper_task and not _reaper_task.done():
        _reaper_task.cancel()
        try:
            await _reaper_task
        except Exception:
            pass
    _reaper_task = None
    for uid in list(_BUFFERS.keys()):
        async with _lock_for(uid):
            await _stop_buffer_unlocked(uid, reason=reason)
    # Orphan dirs from previous crash
    root = instant_replay_root()
    if root.is_dir():
        for child in list(root.iterdir()):
            if child.is_dir() and child.name not in _BUFFERS:
                shutil.rmtree(child, ignore_errors=True)


async def cleanup_camera_instant_replay_buffer(camera_uid: str) -> None:
    uid = (camera_uid or "").strip()
    if not uid:
        return
    async with _lock_for(uid):
        await _stop_buffer_unlocked(uid, reason="camera_removed")
