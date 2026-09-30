"""RDSO 18.3.5 — opt-in 128 simultaneous recording-stream acceptance harness.

Runs OUTSIDE the production schedule/monitor path:
  - does not flip master/schedule fleet bits
  - writes only under a dedicated temporary acceptance directory
  - one FFmpeg process per camera (stream-copy), short segments
  - requires RDSO_128_ACCEPTANCE=1

Do NOT run on low-storage developer workstations.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.services.ffmpeg_util import ffmpeg_bin
from app.services.recording_config import RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS

logger = logging.getLogger(__name__)

ACCEPTANCE_ENV_FLAG = "RDSO_128_ACCEPTANCE"
ACCEPTANCE_FOLDER = "_rdso_128_acceptance"
ACCEPTANCE_MARKER = "rdso_18_3_5_128"


def acceptance_flag_enabled() -> bool:
    return os.getenv(ACCEPTANCE_ENV_FLAG, "").strip().lower() in ("1", "true", "yes")


def require_acceptance_flag() -> None:
    if not acceptance_flag_enabled():
        raise RuntimeError(
            f"Refusing to run: set {ACCEPTANCE_ENV_FLAG}=1 explicitly. "
            "This starts many FFmpeg recorders and must not run accidentally."
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class StreamSlot:
    camera_id: str
    camera_uid: str
    name: str
    ip_address: str
    rtsp_url: str
    worker_id: Optional[int] = None
    process: Optional[asyncio.subprocess.Process] = None
    session_dir: Optional[Path] = None
    error: Optional[str] = None
    started: bool = False
    has_media: bool = False


@dataclass
class AcceptanceReport:
    requested: int = 0
    selected: int = 0
    started_ok: int = 0
    peak_simultaneous_active: int = 0
    with_media: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    duplicate_processes: int = 0
    cpu_percent_peak: Optional[float] = None
    memory_percent_peak: Optional[float] = None
    disk_write_mb_s_peak: Optional[float] = None
    disk_used_mb: Optional[float] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0
    acceptance_dir: Optional[str] = None
    backend_healthy: Optional[bool] = None
    passed: bool = False
    fail_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_streams": self.requested,
            "selected_cameras": self.selected,
            "successfully_started": self.started_ok,
            "simultaneously_active_peak": self.peak_simultaneous_active,
            "sessions_with_hls_media": self.with_media,
            "failed_streams": self.failed,
            "duplicate_processes": self.duplicate_processes,
            "cpu_percent_peak": self.cpu_percent_peak,
            "memory_percent_peak": self.memory_percent_peak,
            "disk_write_mb_s_peak": self.disk_write_mb_s_peak,
            "disk_space_used_mb": self.disk_used_mb,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "acceptance_dir": self.acceptance_dir,
            "backend_healthy": self.backend_healthy,
            "passed": self.passed,
            "fail_reasons": self.fail_reasons,
            "notes": self.notes,
            "rdso_min_streams": RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS,
        }


def _rtsp_timeout_args() -> list[str]:
    flag = "-timeout" if os.name == "nt" else "-stimeout"
    args = [flag, "5000000"]
    if os.name != "nt":
        args.extend(["-rw_timeout", "5000000"])
    return args


def build_acceptance_ffmpeg_cmd(
    *,
    rtsp_url: str,
    session_dir: Path,
    segment_seconds: int,
) -> list[str]:
    """Stream-copy HLS writer scoped to the acceptance folder (no probe / no schedule)."""
    playlist = str(session_dir / "index.m3u8")
    segments = str(session_dir / "seg_%05d.ts")
    return [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-probesize",
        "512000",
        "-analyzeduration",
        "500000",
        "-rtsp_transport",
        "tcp",
        *_rtsp_timeout_args(),
        "-i",
        rtsp_url,
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(max(1, int(segment_seconds))),
        "-hls_list_size",
        "0",
        "-hls_flags",
        "append_list+program_date_time+independent_segments",
        "-hls_segment_filename",
        segments,
        # Marker for orphan cleanup / process matching
        "-metadata",
        f"comment={ACCEPTANCE_MARKER}",
        playlist,
    ]


async def select_acceptance_cameras(
    *,
    count: int,
    prefer_stream: str = "main",
) -> list[StreamSlot]:
    """Pick unique cameras with go2rtc recording RTSP URLs (read-only DB)."""
    from app.core.database import camera_collection
    from app.services.camera_uid import make_camera_uid
    from app.services.go2rtc_service import local_recording_rtsp_url
    from app.services.go2rtc_workers import WORKERS_ENABLED, normalize_worker_id
    from app.services.recording_config import resolve_recording_stream_choice
    from app.services.rtsp_utils import build_camera_rtsp_urls

    slots: list[StreamSlot] = []
    seen_uids: set[str] = set()
    async for cam in camera_collection.find({}).sort("name", 1):
        if len(slots) >= count:
            break
        cam_id = str(cam.get("_id") or "")
        ip = (cam.get("ip_address") or "").strip()
        if not cam_id or not ip:
            continue
        uid = (cam.get("camera_uid") or make_camera_uid(ip) or "").strip()
        if not uid or uid in seen_uids:
            continue
        seen_uids.add(uid)
        stream = prefer_stream
        try:
            stream = resolve_recording_stream_choice(cam)
        except Exception:
            stream = prefer_stream
        worker_id = None
        if WORKERS_ENABLED:
            worker_id = normalize_worker_id(cam.get("worker_id")) or 1
        # Prefer go2rtc restream (existing architecture).
        rtsp = local_recording_rtsp_url(uid, stream, worker_id=worker_id)
        if not rtsp:
            urls = build_camera_rtsp_urls(cam)
            rtsp = (urls.get("main_rtsp_url") if stream == "main" else urls.get("sub_rtsp_url")) or ""
        if not rtsp:
            continue
        slots.append(
            StreamSlot(
                camera_id=cam_id,
                camera_uid=uid,
                name=str(cam.get("name") or ip),
                ip_address=ip,
                rtsp_url=rtsp,
                worker_id=worker_id,
            )
        )
    return slots


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _slot_has_media(session_dir: Optional[Path]) -> bool:
    if not session_dir or not session_dir.is_dir():
        return False
    playlist = session_dir / "index.m3u8"
    if not playlist.is_file() or playlist.stat().st_size < 8:
        return False
    segs = list(session_dir.glob("seg_*.ts"))
    return any(s.is_file() and s.stat().st_size > 0 for s in segs)


def _process_alive(proc: Optional[asyncio.subprocess.Process]) -> bool:
    return proc is not None and proc.returncode is None


async def _probe_backend_health(url: str = "http://127.0.0.1:10000/api/health") -> Optional[bool]:
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except Exception:
        return None


async def start_slot(
    slot: StreamSlot,
    *,
    root: Path,
    segment_seconds: int,
) -> None:
    session_dir = root / slot.camera_uid
    session_dir.mkdir(parents=True, exist_ok=True)
    slot.session_dir = session_dir
    cmd = build_acceptance_ffmpeg_cmd(
        rtsp_url=slot.rtsp_url,
        session_dir=session_dir,
        segment_seconds=segment_seconds,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        slot.process = proc
        slot.started = True
    except Exception as exc:
        slot.error = f"spawn_failed:{exc}"
        slot.started = False


async def stop_slot(slot: StreamSlot) -> None:
    proc = slot.process
    slot.process = None
    if not proc:
        return
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def count_duplicate_pids(slots: list[StreamSlot]) -> int:
    pids = [s.process.pid for s in slots if s.process and s.process.pid]
    return len(pids) - len(set(pids))


async def kill_acceptance_orphan_ffmpeg(acceptance_root: Path) -> int:
    """Best-effort: kill FFmpeg processes whose cmdline references the acceptance dir."""
    killed = 0
    needle = str(acceptance_root).replace("\\", "/").lower()
    marker = ACCEPTANCE_MARKER.lower()
    try:
        import psutil
    except ImportError:
        return 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "ffmpeg" not in name:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).replace("\\", "/").lower()
            if needle in cmdline or marker in cmdline:
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
            continue
    return killed


async def run_128_stream_acceptance(
    *,
    stream_count: int | None = None,
    duration_seconds: int = 45,
    segment_seconds: int = 2,
    stagger_ms: int = 25,
    acceptance_root: Path | None = None,
    prefer_stream: str = "main",
    health_url: str = "http://127.0.0.1:10000/api/health",
) -> AcceptanceReport:
    """Execute the opt-in 128-stream acceptance. Caller must set RDSO_128_ACCEPTANCE=1."""
    require_acceptance_flag()
    target = int(stream_count or RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS)
    report = AcceptanceReport(requested=target)
    report.notes.append(
        "Standalone harness: does not modify recording schedule/master; "
        "uses dedicated temp directory and go2rtc RTSP restream URLs."
    )

    from app.services.storage_settings_store import get_effective_recordings_dir

    run_id = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    root = acceptance_root or (
        get_effective_recordings_dir() / ACCEPTANCE_FOLDER / run_id
    )
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    report.acceptance_dir = str(root)

    # Save nothing about fleet schedule — read-only camera selection.
    slots = await select_acceptance_cameras(count=target, prefer_stream=prefer_stream)
    report.selected = len(slots)
    if len(slots) < target:
        report.fail_reasons.append(
            f"Only {len(slots)} unique cameras available; need {target}"
        )
        report.finished_at = _iso(_utcnow())
        shutil.rmtree(root, ignore_errors=True)
        return report

    started_mono = time.monotonic()
    report.started_at = _iso(_utcnow())
    report.backend_healthy = await _probe_backend_health(health_url)

    # Stagger starts to reduce thundering herd on go2rtc / disk.
    for slot in slots:
        await start_slot(slot, root=root, segment_seconds=segment_seconds)
        if stagger_ms > 0:
            await asyncio.sleep(stagger_ms / 1000.0)

    report.started_ok = sum(1 for s in slots if s.started and _process_alive(s.process))
    for s in slots:
        if not s.started:
            report.failed.append(
                {"camera_uid": s.camera_uid, "ip": s.ip_address, "reason": s.error or "not_started"}
            )

    # Hold window: track peak simultaneous alive + resources
    try:
        import psutil

        psutil.cpu_percent(interval=None)
        disk_io0 = psutil.disk_io_counters()
        t0 = time.monotonic()
    except Exception:
        psutil = None  # type: ignore
        disk_io0 = None
        t0 = time.monotonic()

    hold_until = time.monotonic() + max(5, int(duration_seconds))
    while time.monotonic() < hold_until:
        alive = sum(1 for s in slots if _process_alive(s.process))
        report.peak_simultaneous_active = max(report.peak_simultaneous_active, alive)
        if psutil:
            try:
                report.cpu_percent_peak = max(
                    report.cpu_percent_peak or 0.0, float(psutil.cpu_percent(interval=None))
                )
                report.memory_percent_peak = max(
                    report.memory_percent_peak or 0.0, float(psutil.virtual_memory().percent)
                )
                disk_io1 = psutil.disk_io_counters()
                if disk_io0 and disk_io1:
                    dt = max(0.001, time.monotonic() - t0)
                    write_mb_s = (disk_io1.write_bytes - disk_io0.write_bytes) / dt / 1e6
                    report.disk_write_mb_s_peak = max(
                        report.disk_write_mb_s_peak or 0.0, float(write_mb_s)
                    )
                    disk_io0 = disk_io1
                    t0 = time.monotonic()
            except Exception:
                pass
        await asyncio.sleep(1.0)

    # Final alive + media check
    alive_final = sum(1 for s in slots if _process_alive(s.process))
    report.peak_simultaneous_active = max(report.peak_simultaneous_active, alive_final)
    for s in slots:
        if s.started and not _process_alive(s.process):
            report.failed.append(
                {
                    "camera_uid": s.camera_uid,
                    "ip": s.ip_address,
                    "reason": f"ffmpeg_exited_code_{getattr(s.process, 'returncode', '?')}",
                }
            )
        s.has_media = _slot_has_media(s.session_dir)
    report.with_media = sum(1 for s in slots if s.has_media)
    report.duplicate_processes = count_duplicate_pids(slots)
    report.disk_used_mb = round(_dir_size_bytes(root) / 1e6, 2)
    report.backend_healthy = await _probe_backend_health(health_url)

    # Cleanup: stop recorders, kill orphans, delete temp media
    for s in slots:
        await stop_slot(s)
    orphans = await kill_acceptance_orphan_ffmpeg(root)
    if orphans:
        report.notes.append(f"Killed {orphans} orphan acceptance FFmpeg process(es)")
    shutil.rmtree(root, ignore_errors=True)
    if root.exists():
        report.notes.append(f"Warning: acceptance dir still present: {root}")
    else:
        report.notes.append("Acceptance recordings directory deleted")

    report.duration_seconds = time.monotonic() - started_mono
    report.finished_at = _iso(_utcnow())

    # PASS criteria
    if report.selected < target:
        report.fail_reasons.append("insufficient unique cameras")
    if report.started_ok < target:
        report.fail_reasons.append(f"started_ok={report.started_ok} < {target}")
    if report.peak_simultaneous_active < target:
        report.fail_reasons.append(
            f"peak_simultaneous_active={report.peak_simultaneous_active} < {target}"
        )
    if report.with_media < target:
        report.fail_reasons.append(f"sessions_with_media={report.with_media} < {target}")
    if report.duplicate_processes:
        report.fail_reasons.append(f"duplicate_processes={report.duplicate_processes}")
    if report.backend_healthy is False:
        report.fail_reasons.append("backend /api/health not healthy")

    report.passed = len(report.fail_reasons) == 0
    return report
