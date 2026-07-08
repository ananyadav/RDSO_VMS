"""
Detect and clean orphan FFmpeg processes left after backend restarts.

Only touches FFmpeg whose command line references NVR recording paths
(or legacy nvr_live paths from prior live-HLS runs).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import psutil

from app.services.ffmpeg_util import ffmpeg_bin
from app.services.rtsp_utils import mask_rtsp_url

logger = logging.getLogger(__name__)

FULLSCREEN_SUFFIX = "__fullscreen"
_CHANNEL_RE = re.compile(r"/Streaming/Channels/(\d{3})")
_RTSP_RE = re.compile(r"-i\s+(rtsp://\S+)", re.IGNORECASE)
_LIVE_STREAM_ID_RE = re.compile(
    r"nvr_live[\\/]+([^\\/]+)[\\/]",
    re.IGNORECASE,
)
_RECORDING_CAMERA_RE = re.compile(
    r"Recordings[\\/]+([^\\/]+)[\\/]sessions",
    re.IGNORECASE,
)

SHUTDOWN_WAIT_SECONDS = float(os.getenv("FFMPEG_SHUTDOWN_WAIT_SECONDS", "3"))


@dataclass
class NvrFfmpegProcess:
    pid: int
    parent_pid: Optional[int]
    parent_alive: bool
    cmdline: str
    stream_type: str  # grid | fullscreen | recording | unknown
    stream_id: Optional[str] = None
    camera_id: Optional[str] = None
    channel: Optional[str] = None
    rtsp_url_masked: Optional[str] = None
    status: str = "orphan"  # tracked | orphan
    has_rtsp_453: bool = False


def _recordings_dir_str() -> str:
    from app.services.video_recording import RECORDINGS_DIR

    return str(RECORDINGS_DIR)


def _ffmpeg_names() -> Set[str]:
    base = os.path.basename(ffmpeg_bin()).lower()
    names = {base, "ffmpeg", "ffmpeg.exe"}
    return names


def is_nvr_ffmpeg_cmdline(cmdline: str) -> bool:
    """True when cmdline is our recording FFmpeg or legacy live HLS FFmpeg."""
    if not cmdline:
        return False
    low = cmdline.lower()
    if "nvr_live" in low:
        return True
    rec = _recordings_dir_str().lower()
    if rec and rec in low:
        return True
    if "recordings" in low and ("seg_" in low or "index.m3u8" in low):
        return True
    return False


def _parse_nvr_ffmpeg(cmdline: str, pid: int, parent_pid: Optional[int]) -> NvrFfmpegProcess:
    stream_id = None
    camera_id = None
    stream_type = "unknown"
    channel = None
    rtsp_masked = None
    has_453 = "453" in cmdline or "not enough bandwidth" in cmdline.lower()

    live_match = _LIVE_STREAM_ID_RE.search(cmdline)
    if live_match:
        stream_id = live_match.group(1)
        camera_id = (
            stream_id[: -len(FULLSCREEN_SUFFIX)]
            if stream_id.endswith(FULLSCREEN_SUFFIX)
            else stream_id
        )
        stream_type = "fullscreen" if stream_id.endswith(FULLSCREEN_SUFFIX) else "grid"
    else:
        rec_match = _RECORDING_CAMERA_RE.search(cmdline)
        if rec_match:
            camera_id = rec_match.group(1)
            stream_id = f"{camera_id}/recording"
            stream_type = "recording"

    rtsp_match = _RTSP_RE.search(cmdline)
    if rtsp_match:
        rtsp_url = rtsp_match.group(1).strip("'\"")
        rtsp_masked = mask_rtsp_url(rtsp_url)
        ch = _CHANNEL_RE.search(rtsp_url)
        if ch:
            channel = ch.group(1)

    parent_alive = False
    if parent_pid is not None:
        parent_alive = psutil.pid_exists(parent_pid)

    return NvrFfmpegProcess(
        pid=pid,
        parent_pid=parent_pid,
        parent_alive=parent_alive,
        cmdline=cmdline,
        stream_type=stream_type,
        stream_id=stream_id,
        camera_id=camera_id,
        channel=channel,
        rtsp_url_masked=rtsp_masked,
        has_rtsp_453=has_453,
    )


def get_tracked_ffmpeg_pids() -> Set[int]:
    """PIDs owned by active recording sessions."""
    pids: Set[int] = set()
    try:
        from app.services.video_recording import ACTIVE_RECORDINGS

        for entry in ACTIVE_RECORDINGS.values():
            recorder = entry.get("recorder")
            proc = getattr(recorder, "recording_process", None)
            if proc and proc.returncode is None:
                pids.add(proc.pid)
    except Exception:
        pass
    return pids


def _process_parent_pid(proc: psutil.Process) -> Optional[int]:
    try:
        return proc.ppid()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def list_nvr_ffmpeg_processes() -> List[NvrFfmpegProcess]:
    """Enumerate NVR FFmpeg processes and classify tracked vs orphan."""
    tracked = get_tracked_ffmpeg_pids()
    current_pid = os.getpid()
    results: List[NvrFfmpegProcess] = []
    ffmpeg_names = _ffmpeg_names()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name not in ffmpeg_names and "ffmpeg" not in name:
                continue
            cmdline_list = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_list) if isinstance(cmdline_list, list) else str(cmdline_list)
            if not is_nvr_ffmpeg_cmdline(cmdline):
                continue
            pid = proc.info["pid"]
            parent_pid = _process_parent_pid(psutil.Process(pid))
            info = _parse_nvr_ffmpeg(cmdline, pid, parent_pid)
            if pid in tracked:
                info.status = "tracked"
            elif not info.parent_alive:
                info.status = "orphan"
            elif parent_pid == current_pid:
                info.status = "orphan"
            else:
                info.status = "external"
            results.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return results


def _kill_pid_sync(pid: int, *, wait_seconds: float = SHUTDOWN_WAIT_SECONDS) -> bool:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return True
    except Exception as exc:
        logger.warning("[FFMPEG-CLEANUP] terminate pid=%s failed: %s", pid, exc)
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            return True
        return True

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.15)

    if psutil.pid_exists(pid):
        try:
            psutil.Process(pid).kill()
            logger.warning("[FFMPEG-CLEANUP] force-killed pid=%s", pid)
        except psutil.NoSuchProcess:
            pass
    return not psutil.pid_exists(pid)


async def kill_pids(pids: List[int], *, wait_seconds: float = SHUTDOWN_WAIT_SECONDS) -> List[int]:
    """Terminate then force-kill PIDs; returns PIDs confirmed dead."""
    if not pids:
        return []
    loop = asyncio.get_running_loop()
    killed: List[int] = []
    for pid in pids:
        ok = await loop.run_in_executor(
            None, lambda p=pid: _kill_pid_sync(p, wait_seconds=wait_seconds)
        )
        if ok:
            killed.append(pid)
            logger.info("[FFMPEG-CLEANUP] killed pid=%s", pid)
    return killed


def _orphan_processes(processes: Optional[List[NvrFfmpegProcess]] = None) -> List[NvrFfmpegProcess]:
    procs = processes if processes is not None else list_nvr_ffmpeg_processes()
    return [p for p in procs if p.status == "orphan"]


def cleanup_orphan_ffmpeg_on_startup() -> List[int]:
    """Sync startup hook — kill orphan NVR FFmpeg from prior backend runs."""
    orphans = _orphan_processes()
    if not orphans:
        logger.info("[FFMPEG-CLEANUP] startup: no orphan FFmpeg processes")
        return []
    pids = [p.pid for p in orphans]
    logger.warning(
        "[FFMPEG-CLEANUP] startup: killing %s orphan FFmpeg process(es): %s",
        len(pids),
        pids,
    )
    killed: List[int] = []
    for pid in pids:
        if _kill_pid_sync(pid):
            killed.append(pid)
            logger.info("[FFMPEG-CLEANUP] startup killed pid=%s", pid)
    return killed


async def cleanup_orphan_ffmpeg() -> dict:
    """Kill orphan NVR FFmpeg processes (admin API)."""
    orphans = _orphan_processes()
    pids = [p.pid for p in orphans]
    killed = await kill_pids(pids)
    return {
        "orphanCount": len(orphans),
        "killedPids": killed,
        "requestedPids": pids,
    }


async def shutdown_all_nvr_ffmpeg() -> List[int]:
    """On backend shutdown: stop tracked children then sweep remaining NVR FFmpeg."""
    tracked = list(get_tracked_ffmpeg_pids())
    if tracked:
        logger.info("[FFMPEG-CLEANUP] shutdown: stopping tracked FFmpeg %s", tracked)
        await kill_pids(tracked)

    remaining = _orphan_processes()
    extra_pids = [p.pid for p in remaining if p.pid not in tracked]
    if extra_pids:
        logger.info("[FFMPEG-CLEANUP] shutdown: sweeping %s remaining NVR FFmpeg", extra_pids)
        await kill_pids(extra_pids)
        tracked.extend(extra_pids)
    return tracked


def nvr_ffmpeg_to_dict(proc: NvrFfmpegProcess) -> dict:
    return {
        "pid": proc.pid,
        "cameraId": proc.camera_id,
        "streamId": proc.stream_id,
        "streamType": proc.stream_type,
        "channel": proc.channel,
        "rtspUrl": proc.rtsp_url_masked,
        "status": proc.status,
        "parentPid": proc.parent_pid,
        "parentAlive": proc.parent_alive,
    }


def get_orphans_report() -> dict:
    processes = list_nvr_ffmpeg_processes()
    orphans = [p for p in processes if p.status == "orphan"]
    tracked = [p for p in processes if p.status == "tracked"]
    return {
        "processes": [nvr_ffmpeg_to_dict(p) for p in processes],
        "orphanCount": len(orphans),
        "trackedCount": len(tracked),
        "orphans": [nvr_ffmpeg_to_dict(p) for p in orphans],
    }
