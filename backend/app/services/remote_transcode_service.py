"""RDSO 18.5(ii) — on-demand remote live/playback transcoding via FFmpeg.

Live input: go2rtc local RTSP (never camera credentials).
Playback input: existing recording session HLS/files.
Output: temporary HLS under a per-job directory; cleaned when the job ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.services.ffmpeg_util import ffmpeg_bin, ffprobe_bin
from app.services.go2rtc_service import local_recording_rtsp_url
from app.services.remote_transcode_profiles import (
    BandwidthProfile,
    remote_transcode_capability_public,
)

logger = logging.getLogger(__name__)

_JOBS: dict[str, "RemoteTranscodeJob"] = {}
_LOCK = asyncio.Lock()

IDLE_TTL_SEC = max(30, int(os.getenv("REMOTE_TRANSCODE_IDLE_TTL_SEC", "120") or 120))
HLS_TIME_SEC = max(1, int(os.getenv("REMOTE_TRANSCODE_HLS_TIME_SEC", "2") or 2))
HLS_LIST_SIZE = max(3, int(os.getenv("REMOTE_TRANSCODE_HLS_LIST_SIZE", "6") or 6))


def remote_transcode_root() -> Path:
    raw = (os.getenv("REMOTE_TRANSCODE_DIR") or "").strip()
    if raw:
        root = Path(raw).resolve()
    else:
        from app.services.video_recording import RECORDINGS_DIR

        root = (RECORDINGS_DIR / "_remote_transcode").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _input_is_rtsp(url: str) -> bool:
    return (url or "").lower().startswith("rtsp://")


@dataclass
class RemoteTranscodeJob:
    job_id: str
    kind: str  # live | playback | file
    camera_id: str
    camera_uid: str
    user_id: str
    profile: BandwidthProfile
    output_dir: Path
    input_url: str
    mode: str
    bandwidth_kbps: Optional[float] = None
    recording_session_id: Optional[str] = None
    process: Optional[asyncio.subprocess.Process] = None
    stderr_tail: str = ""
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    _stderr_task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def playlist_path(self) -> Path:
        return self.output_dir / "index.m3u8"

    def touch(self) -> None:
        self.last_seen_at = time.time()

    def public(self) -> dict[str, Any]:
        alive = bool(self.process and self.process.returncode is None)
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "camera_id": self.camera_id,
            "camera_uid": self.camera_uid,
            "recording_session_id": self.recording_session_id,
            "mode": self.mode,
            "bandwidth_kbps": self.bandwidth_kbps,
            "profile": self.profile.public(),
            "playlist_url": f"/api/remote-transcode/sessions/{self.job_id}/media/index.m3u8",
            "alive": alive,
            "error": self.error,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "input_source_kind": (
                "go2rtc_local_rtsp"
                if self.kind == "live"
                else ("recording_session" if self.kind == "playback" else "file")
            ),
            "input_is_go2rtc_local": self.kind == "live" and "127.0.0.1" in (self.input_url or ""),
            "rdso_18_5_ii": True,
        }


def build_ffmpeg_transcode_cmd(
    *,
    input_url: str,
    output_playlist: Path,
    profile: BandwidthProfile,
    segment_pattern: str,
) -> list[str]:
    """Build FFmpeg argv that actually re-encodes (fps/resolution/bitrate)."""
    vf = (
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease,"
        f"fps={profile.fps}"
    )
    bitrate = f"{profile.video_bitrate_kbps}k"
    bufsize = f"{profile.video_bitrate_kbps * 2}k"
    gop = max(profile.fps * 2, 10)

    cmd = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
    ]
    if _input_is_rtsp(input_url):
        cmd.extend(["-rtsp_transport", "tcp", "-stimeout", "5000000"])
    cmd.extend(
        [
            "-i",
            input_url,
            "-an",
            "-c:v",
            profile.codec,
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            profile.pixel_format,
            "-vf",
            vf,
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            bufsize,
            "-g",
            str(gop),
            "-f",
            "hls",
            "-hls_time",
            str(HLS_TIME_SEC),
            "-hls_list_size",
            str(HLS_LIST_SIZE),
            "-hls_flags",
            "delete_segments+independent_segments+program_date_time",
            "-hls_segment_filename",
            segment_pattern,
            str(output_playlist),
        ]
    )
    return cmd


def resolve_live_input_url(
    camera_uid: str,
    *,
    stream: str = "main",
    worker_id: Optional[int] = None,
) -> str:
    """Live must come through VMS/go2rtc — never camera RTSP with credentials."""
    uid = (camera_uid or "").strip()
    if not uid:
        raise ValueError("camera_uid required for live transcode")
    return local_recording_rtsp_url(uid, stream, worker_id=worker_id)


async def resolve_playback_input_path(camera_id: str, recording_session_id: str) -> Path:
    from app.services.recording_media import resolve_session_dir

    session_dir = await resolve_session_dir(camera_id, recording_session_id)
    for name in ("index.m3u8", "video.mp4", "export.mp4"):
        candidate = session_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No playable media in recording session {recording_session_id}")


async def _read_stderr(job: RemoteTranscodeJob) -> None:
    proc = job.process
    if not proc or not proc.stderr:
        return
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                job.stderr_tail = (job.stderr_tail + "\n" + text)[-2000:]
                logger.warning("[remote-transcode][%s] %s", job.job_id, text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("[remote-transcode] stderr reader: %s", exc)


async def start_transcode_job(
    *,
    kind: str,
    camera_id: str,
    camera_uid: str,
    user_id: str,
    profile: BandwidthProfile,
    input_url: str,
    mode: str,
    bandwidth_kbps: Optional[float] = None,
    recording_session_id: Optional[str] = None,
) -> RemoteTranscodeJob:
    job_id = secrets.token_urlsafe(12)
    out_dir = remote_transcode_root() / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = out_dir / "index.m3u8"
    segment_pattern = str(out_dir / "seg_%05d.ts")

    job = RemoteTranscodeJob(
        job_id=job_id,
        kind=kind,
        camera_id=camera_id,
        camera_uid=camera_uid,
        user_id=user_id,
        profile=profile,
        output_dir=out_dir,
        input_url=input_url,
        mode=mode,
        bandwidth_kbps=bandwidth_kbps,
        recording_session_id=recording_session_id,
    )

    cmd = build_ffmpeg_transcode_cmd(
        input_url=input_url,
        output_playlist=playlist,
        profile=profile,
        segment_pattern=segment_pattern,
    )
    logger.info(
        "[remote-transcode] start job=%s kind=%s profile=%s camera=%s",
        job_id,
        kind,
        profile.id,
        camera_uid or camera_id,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise RuntimeError("FFmpeg not found — cannot transcode remote media")
    except Exception as exc:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to start FFmpeg: {exc}") from exc

    job.process = proc
    job._stderr_task = asyncio.create_task(_read_stderr(job))

    async with _LOCK:
        _JOBS[job_id] = job
    return job


async def _terminate_job(job: RemoteTranscodeJob, *, reason: str) -> None:
    logger.info("[remote-transcode] stop job=%s reason=%s", job.job_id, reason)
    if job._stderr_task and not job._stderr_task.done():
        job._stderr_task.cancel()
        try:
            await job._stderr_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    proc = job.process
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception as exc:
            logger.warning("[remote-transcode] terminate %s: %s", job.job_id, exc)
    try:
        if job.output_dir.exists():
            shutil.rmtree(job.output_dir, ignore_errors=True)
    except Exception as exc:
        logger.warning("[remote-transcode] cleanup %s: %s", job.job_id, exc)


async def stop_transcode_job(job_id: str, *, reason: str = "client_stop") -> bool:
    async with _LOCK:
        job = _JOBS.pop(job_id, None)
    if not job:
        return False
    await _terminate_job(job, reason=reason)
    return True


def get_job(job_id: str) -> Optional[RemoteTranscodeJob]:
    return _JOBS.get(job_id)


def list_jobs_for_user(user_id: str) -> list[RemoteTranscodeJob]:
    return [j for j in _JOBS.values() if j.user_id == user_id]


async def heartbeat_job(job_id: str, *, user_id: Optional[str] = None) -> Optional[RemoteTranscodeJob]:
    job = _JOBS.get(job_id)
    if not job:
        return None
    if user_id and job.user_id != user_id:
        return None
    job.touch()
    if job.process and job.process.returncode is not None and not job.error:
        job.error = f"ffmpeg_exited_{job.process.returncode}"
        if job.stderr_tail:
            job.error = f"{job.error}: {job.stderr_tail[-200:]}"
    return job


async def cleanup_idle_jobs() -> int:
    now = time.time()
    to_stop: list[RemoteTranscodeJob] = []
    async with _LOCK:
        for jid, job in list(_JOBS.items()):
            dead = job.process is not None and job.process.returncode is not None
            idle = (now - job.last_seen_at) > IDLE_TTL_SEC
            if dead or idle:
                to_stop.append(_JOBS.pop(jid))
    for job in to_stop:
        reason = (
            "ffmpeg_dead"
            if (job.process and job.process.returncode is not None)
            else "idle_ttl"
        )
        await _terminate_job(job, reason=reason)
    return len(to_stop)


async def stop_all_jobs_for_user(user_id: str) -> int:
    to_stop: list[RemoteTranscodeJob] = []
    async with _LOCK:
        for jid, job in list(_JOBS.items()):
            if job.user_id == user_id:
                to_stop.append(_JOBS.pop(jid))
    for job in to_stop:
        await _terminate_job(job, reason="user_logout_or_revoke")
    return len(to_stop)


def reset_jobs_for_tests() -> None:
    """Test helper — drops in-memory map without waiting on FFmpeg."""
    _JOBS.clear()


def probe_media(path: Path) -> dict[str, Any]:
    """ffprobe summary for acceptance (fps / resolution / codec / bitrate)."""
    cmd = [
        ffprobe_bin(),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=30)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
    except Exception as exc:
        return {"ok": False, "error": f"ffprobe_json: {exc}"}

    video = None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            video = s
            break
    if not video:
        return {"ok": False, "error": "no_video_stream", "raw": data}

    fps_raw = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        num, den = fps_raw.split("/", 1)
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0

    bit_rate = video.get("bit_rate") or (data.get("format") or {}).get("bit_rate")
    try:
        bit_rate_i = int(bit_rate) if bit_rate else None
    except Exception:
        bit_rate_i = None

    return {
        "ok": True,
        "codec": video.get("codec_name"),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "bit_rate": bit_rate_i,
        "duration": float((data.get("format") or {}).get("duration") or 0) or None,
    }


async def run_file_transcode_for_acceptance(
    input_path: Path,
    profile: BandwidthProfile,
    output_dir: Path,
    *,
    duration_sec: float = 4.0,
) -> dict[str, Any]:
    """Transcode synthetic/local media and probe output (acceptance harness)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist = output_dir / "index.m3u8"
    segment_pattern = str(output_dir / "seg_%05d.ts")
    cmd = build_ffmpeg_transcode_cmd(
        input_url=str(input_path),
        output_playlist=playlist,
        profile=profile,
        segment_pattern=segment_pattern,
    )
    if "-i" in cmd:
        i = cmd.index("-i")
        cmd[i:i] = ["-t", str(duration_sec)]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace")[-500:]
        return {"ok": False, "error": f"ffmpeg_exit_{proc.returncode}", "stderr": err}

    segments = sorted(output_dir.glob("seg_*.ts"))
    probe_target = segments[0] if segments else playlist
    if not probe_target.exists():
        return {"ok": False, "error": "no_output_media"}
    probe = probe_media(probe_target)
    return {
        "ok": bool(probe.get("ok")),
        "profile": profile.public(),
        "playlist": str(playlist),
        "probe": probe,
        "segment_count": len(segments),
    }


def capability_with_runtime() -> dict[str, Any]:
    cap = remote_transcode_capability_public()
    cap["active_jobs"] = len(_JOBS)
    cap["idle_ttl_sec"] = IDLE_TTL_SEC
    cap["ffmpeg"] = ffmpeg_bin()
    return cap
