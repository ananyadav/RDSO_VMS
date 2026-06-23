"""
Live HLS — one FFmpeg per camera, copy only (no CPU transcode).

Stable baseline (Phase 3):
  Grid      — always substream channel 102 (H.264 copy)
  Fullscreen — main channel 101 (copy) with automatic fallback to sub/102
  No MP4, no CPU transcode, no H.265 re-encode on the grid path.

Registry (Phase 3 Step 2):
  One FFmpeg per stream id; ref-counted subscribe/unsubscribe with warm stop.

Env:
  HLS_LIVE_STREAM — legacy; grid ignores non-sub values (grid is always 102)
  HLS_FULLSCREEN_STREAM — main | preview | sub (default main → 101, fallback 102)
  HLS_KEEP_WARM_SECONDS / HLS_WARM_SECONDS — warm stop after unsubscribe (default 30)
  LIVE_BATCH_SIZE / HLS_BATCH_SIZE — grid batch start size (default 4)
  LIVE_BATCH_DELAY_MS / HLS_BATCH_DELAY_MS — delay between batches (default 750)
"""

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from app.core.database import camera_collection
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.ffmpeg_orphan_cleanup import build_ffmpeg_diagnostics_extra
from app.services.live_stream_registry import REGISTRY, StreamRecord, StreamStatus
from app.services.live_latency import (
    FRONTEND_TELEMETRY,
    build_stream_latency,
    is_rtsp_connected_stderr,
    mark_first_segment_created,
    mark_playlist_created,
    mark_playlist_ready,
    mark_rtsp_connected,
)
from app.services.rtsp_utils import build_camera_rtsp_urls, mask_rtsp_url


def _env_first(*keys: str, default: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default

if os.getenv("NVR_LIVE_DIR"):
    LIVE_DIR = Path(os.getenv("NVR_LIVE_DIR")).resolve()
else:
    LIVE_DIR = Path(tempfile.gettempdir()) / "nvr_live"
LIVE_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG = ffmpeg_bin()
HLS_SEGMENT_SECONDS = _env_first("HLS_SEGMENT_SECONDS", default="1")
HLS_LIST_SIZE = _env_first("HLS_LIST_SIZE", default="3")
READY_TIMEOUT = float(os.getenv("HLS_READY_TIMEOUT", "4"))
LIVE_STREAM = os.getenv("HLS_LIVE_STREAM", "sub").strip().lower()
FULLSCREEN_STREAM = os.getenv("HLS_FULLSCREEN_STREAM", "main").strip().lower()
FULLSCREEN_SUFFIX = "__fullscreen"
RTSP_TIMEOUT_US = os.getenv("HLS_RTSP_TIMEOUT_US", "5000000")
_START_SEM = asyncio.Semaphore(int(os.getenv("HLS_MAX_CONCURRENT_STARTS", "8")))
BATCH_SIZE = int(_env_first("LIVE_BATCH_SIZE", "HLS_BATCH_SIZE", default="4"))
BATCH_DELAY_SEC = float(_env_first("LIVE_BATCH_DELAY_MS", "HLS_BATCH_DELAY_MS", default="750")) / 1000.0
# Live HLS: no append_list. Windows skips delete_segments (file locks → player 404 stalls).
if os.name == "nt":
    HLS_FLAGS = "omit_endlist+independent_segments"
else:
    HLS_FLAGS = "delete_segments+omit_endlist+independent_segments"

logging.info(
    f"[HLS] dir={LIVE_DIR}, ffmpeg={FFMPEG}, segment={HLS_SEGMENT_SECONDS}s, "
    f"list_size={HLS_LIST_SIZE}, flags={HLS_FLAGS}, "
    f"grid=sub/102, fullscreen={FULLSCREEN_STREAM}, max_starts={_START_SEM._value}, "
    f"batch_size={BATCH_SIZE}, batch_delay_ms={int(BATCH_DELAY_SEC * 1000)}"
)


def _clear_stream_dir(cam_dir: Path) -> None:
    for pattern in ("*.ts", "live.m3u8"):
        for f in cam_dir.glob(pattern):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


async def _watch_stream_latency(stream_id: str, cam_dir: Path) -> None:
    """Poll HLS output files for latency milestones (measurement only)."""
    playlist = cam_dir / "live.m3u8"
    deadline = time.monotonic() + max(READY_TIMEOUT * 3, 30.0)
    try:
        while time.monotonic() < deadline:
            record = REGISTRY.get(stream_id)
            if not record or not record.is_process_alive():
                return

            if playlist.exists() and record.playlist_created_wall is None:
                mark_playlist_created(record)

            if record.first_segment_created_wall is None:
                segments = list(cam_dir.glob("seg*.ts"))
                if segments:
                    mark_first_segment_created(record)

            if record.playlist_ready_wall is None and playlist.exists():
                try:
                    text = playlist.read_text(encoding="utf-8", errors="ignore")
                    if "#EXTM3U" in text and ".ts" in text:
                        mark_playlist_ready(record)
                except OSError:
                    pass

            if (
                record.playlist_ready_wall is not None
                and record.first_segment_created_wall is not None
            ):
                return
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        pass


def _start_latency_watch(stream_id: str, cam_dir: Path, record: StreamRecord) -> None:
    if record.latency_watch_task and not record.latency_watch_task.done():
        record.latency_watch_task.cancel()
    record.latency_watch_task = asyncio.create_task(
        _watch_stream_latency(stream_id, cam_dir)
    )


def _base_camera_id(stream_id: str) -> str:
    if stream_id.endswith(FULLSCREEN_SUFFIX):
        return stream_id[: -len(FULLSCREEN_SUFFIX)]
    return stream_id


def _is_fullscreen_stream(stream_id: str) -> bool:
    return stream_id.endswith(FULLSCREEN_SUFFIX)


def _playlist_path(stream_id: str) -> Path:
    return LIVE_DIR / stream_id / "live.m3u8"


def _playlist_url(stream_id: str) -> str:
    return f"/api/live/{stream_id}/live.m3u8"


@dataclass
class SubscribeResult:
    ok: bool
    reused: bool = False
    error: Optional[str] = None


def _pick_grid_urls(cam: dict) -> Tuple[Optional[str], str]:
    """Grid baseline: always substream 102, H.264 copy (no transcode)."""
    sub = cam.get("sub_rtsp_url")
    return sub, "sub/102"


def _pick_fullscreen_urls(
    cam: dict,
    *,
    force_sub: bool = False,
) -> Tuple[Optional[str], str]:
    """
    Fullscreen: main/101 when stable (copy only), else fall back to sub/102.
    Optional HLS_FULLSCREEN_STREAM=preview uses ch 103, then sub/102.
    """
    sub = cam.get("sub_rtsp_url")
    main = cam.get("main_rtsp_url")
    preview = cam.get("preview_rtsp_url")

    if force_sub:
        return sub, "sub/102"

    mode = FULLSCREEN_STREAM
    if mode == "sub":
        return sub, "sub/102"
    if mode == "preview":
        if preview:
            return preview, "preview/103"
        return sub, "sub/102 (no preview)"
    if main:
        return main, "main/101"
    return sub, "sub/102 (no main)"


def _pick_live_urls(
    cam: dict,
    *,
    stream_id: str,
    force_sub: bool = False,
) -> Tuple[Optional[str], str]:
    if _is_fullscreen_stream(stream_id):
        return _pick_fullscreen_urls(cam, force_sub=force_sub)
    return _pick_grid_urls(cam)


async def _persist_rtsp_urls(camera_id: str, urls: dict) -> None:
    keys = (
        "main_rtsp_url",
        "sub_rtsp_url",
        "preview_rtsp_url",
        "preview_channel",
        "rtsp_url",
        "recording_channel",
    )
    try:
        await camera_collection.update_one(
            {"_id": ObjectId(camera_id)},
            {"$set": {k: urls[k] for k in keys if k in urls}},
        )
    except Exception as e:
        logging.warning(f"[HLS] Could not persist RTSP URLs for {camera_id}: {e}")


async def _get_camera_doc(camera_id: str) -> Optional[dict]:
    try:
        cam = await camera_collection.find_one({"_id": ObjectId(camera_id)})
        if not cam:
            logging.error(f"[HLS] Camera {camera_id} not found")
            return None
        if not cam.get("sub_rtsp_url") and (cam.get("ip_address") or "").strip():
            urls = build_camera_rtsp_urls(cam)
            await _persist_rtsp_urls(camera_id, urls)
            cam = {**cam, **urls}
        return cam
    except Exception as e:
        logging.error(f"[HLS] DB error for {camera_id}: {e}")
        return None


def _build_ffmpeg_cmd(rtsp_url: str, cam_dir: Path, playlist: Path) -> list:
    # -hls_time is a target; with -c:v copy segments align to camera keyframes (GOP).
    rtsp_timeout_flag = "-timeout" if os.name == "nt" else "-stimeout"
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-probesize",
        "512000",
        "-analyzeduration",
        "500000",
        "-fflags",
        "+genpts+discardcorrupt+nobuffer",
        "-flags",
        "low_delay",
        "-rtsp_transport",
        "tcp",
        rtsp_timeout_flag,
        RTSP_TIMEOUT_US,
        "-i",
        rtsp_url,
        "-an",
        "-c:v",
        "copy",
        "-muxdelay",
        "0",
        "-muxpreload",
        "0",
        "-f",
        "hls",
        "-hls_time",
        HLS_SEGMENT_SECONDS,
        "-hls_list_size",
        HLS_LIST_SIZE,
        "-hls_flags",
        HLS_FLAGS,
        "-hls_segment_filename",
        str(cam_dir / "seg%05d.ts"),
        str(playlist),
    ]
    return cmd


async def _kill_process(record: StreamRecord) -> None:
    proc = record.proc
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    record.proc = None


async def _stop_stream(stream_id: str) -> None:
    await REGISTRY.stop_and_remove(stream_id, kill_process=_kill_process)


async def _stop_ffmpeg_unlocked(stream_id: str) -> None:
    record = REGISTRY.get(stream_id)
    if not record:
        return
    REGISTRY.tear_down_process(record)
    if record.latency_watch_task and not record.latency_watch_task.done():
        record.latency_watch_task.cancel()
    record.latency_watch_task = None
    await _kill_process(record)
    if record.ref_count <= 0:
        record.status = StreamStatus.STOPPED
    logging.info(f"[HLS][registry] stream stopped streamId={stream_id} (process teardown)")


async def _start_ffmpeg(
    stream_id: str,
    *,
    fresh: bool = True,
    force_sub: bool = False,
) -> bool:
    camera_id = _base_camera_id(stream_id)
    cam = await _get_camera_doc(camera_id)
    if not cam:
        return False

    rtsp_url, label = _pick_live_urls(cam, stream_id=stream_id, force_sub=force_sub)
    if not rtsp_url:
        record = REGISTRY.get(stream_id)
        if record:
            REGISTRY.set_error(record, "missing RTSP URL")
        logging.error(f"[HLS] {stream_id} missing RTSP URL")
        return False

    cam_dir = LIVE_DIR / stream_id
    cam_dir.mkdir(parents=True, exist_ok=True)
    if fresh:
        _clear_stream_dir(cam_dir)
    playlist = _playlist_path(stream_id)
    record = REGISTRY.ensure_record(stream_id, playlist)
    record.status = StreamStatus.STARTING

    logging.info(
        f"[HLS] Starting {stream_id} ({label}, copy): {mask_rtsp_url(rtsp_url)}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_ffmpeg_cmd(rtsp_url, cam_dir, playlist),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        REGISTRY.set_error(record, f"FFmpeg not found at {FFMPEG}")
        logging.error(f"[HLS] FFmpeg not found at '{FFMPEG}'")
        return False
    except Exception as e:
        REGISTRY.set_error(record, str(e))
        logging.error(f"[HLS] Failed to start {stream_id}: {e}")
        return False

    async def _log_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            msg = line.decode("utf-8", errors="ignore").strip()
            if msg:
                if is_rtsp_connected_stderr(msg):
                    rec = REGISTRY.get(stream_id)
                    if rec:
                        mark_rtsp_connected(rec)
                if "error" in msg.lower() or "failed" in msg.lower():
                    logging.warning(f"[HLS][ffmpeg][{stream_id}] {msg}")
                    if "453" in msg or "not enough bandwidth" in msg.lower():
                        rec = REGISTRY.get(stream_id)
                        if rec:
                            REGISTRY.set_error(rec, msg[:300])

    async def _monitor():
        stderr_task = asyncio.create_task(_log_stderr())
        rc = await proc.wait()
        stderr_task.cancel()

        async with REGISTRY.lock(stream_id):
            record = REGISTRY.get(stream_id)
            if not record or record.proc is not proc:
                return
            if not record.should_keep_ffmpeg():
                REGISTRY.tear_down_process(record)
                await _kill_process(record)
                REGISTRY.remove_record(stream_id)
                logging.info(
                    f"[HLS][registry] stream stopped streamId={stream_id} "
                    f"(ffmpeg exited during warm, rc={rc})"
                )
                return

            if rc != 0:
                err_text = (record.last_error or "").lower()
                is_453 = "453" in err_text or "not enough bandwidth" in err_text
                if not record.last_error:
                    REGISTRY.set_error(record, f"ffmpeg exited rc={rc}")
                if record.use_main and not record.force_sub and is_453:
                    logging.warning(
                        f"[HLS] {stream_id} main/101 RTSP 453, "
                        "falling back to sub/102"
                    )
                    await _stop_ffmpeg_unlocked(stream_id)
                    async with _START_SEM:
                        await _start_ffmpeg(stream_id, fresh=True, force_sub=True)
                    return
                if record.use_preview and not record.force_sub and is_453:
                    logging.warning(
                        f"[HLS] {stream_id} preview RTSP 453, "
                        "falling back to sub/102"
                    )
                    await _stop_ffmpeg_unlocked(stream_id)
                    async with _START_SEM:
                        await _start_ffmpeg(stream_id, fresh=True, force_sub=True)
                    return
                logging.warning(
                    f"[HLS] FFmpeg exited {stream_id} (rc={rc}), restart..."
                )
                await asyncio.sleep(1.5)
                current = REGISTRY.get(stream_id)
                if current and current.should_keep_ffmpeg():
                    async with _START_SEM:
                        await _start_ffmpeg(
                            stream_id,
                            fresh=False,
                            force_sub=current.force_sub,
                        )
                return

            logging.warning(
                f"[HLS] FFmpeg exited {stream_id} (rc={rc}), restart..."
            )
            await asyncio.sleep(1.5)
            current = REGISTRY.get(stream_id)
            if current and current.should_keep_ffmpeg():
                async with _START_SEM:
                    await _start_ffmpeg(
                        stream_id,
                        fresh=False,
                        force_sub=current.force_sub,
                    )

    use_preview = not force_sub and label.startswith("preview")
    use_main = not force_sub and label.startswith("main")
    monitor_task = asyncio.create_task(_monitor())
    REGISTRY.mark_started(
        record,
        proc,
        use_preview=use_preview,
        use_main=use_main,
        force_sub=force_sub,
        stream_label=label,
        monitor_task=monitor_task,
    )
    ch = label.split("/")[1].split()[0] if "/" in label else "?"
    logging.info(
        "[RTSP] cameraId=%s streamId=%s streamType=%s channel=%s ffmpegPid=%s url=%s",
        camera_id,
        stream_id,
        "fullscreen" if _is_fullscreen_stream(stream_id) else "grid",
        ch,
        proc.pid,
        mask_rtsp_url(rtsp_url),
    )
    logging.info(
        "[HLS][latency] ffmpeg start streamId=%s pid=%s wall=%s",
        stream_id,
        proc.pid,
        record.started_at_wall,
    )
    _start_latency_watch(stream_id, cam_dir, record)
    return True


async def wait_for_playlist(
    stream_id: str,
    timeout: float = READY_TIMEOUT,
    poll_interval: float = 0.08,
) -> bool:
    playlist = _playlist_path(stream_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if playlist.exists():
            try:
                text = playlist.read_text(encoding="utf-8", errors="ignore")
                if "#EXTM3U" in text and ".ts" in text:
                    return True
            except OSError:
                pass
        await asyncio.sleep(poll_interval)
    return False


async def subscribe(
    stream_id: str,
    *,
    wait_ready: bool = True,
    force_sub: bool = False,
) -> SubscribeResult:
    reused = False

    async with REGISTRY.lock(stream_id):
        record = REGISTRY.get(stream_id)
        if record and record.is_process_alive():
            if force_sub and not record.force_sub and _is_fullscreen_stream(stream_id):
                await _stop_ffmpeg_unlocked(stream_id)
                async with _START_SEM:
                    ok = await _start_ffmpeg(stream_id, fresh=True, force_sub=True)
                if not ok:
                    return SubscribeResult(
                        ok=False,
                        error="No RTSP URL or camera offline",
                    )
                record = REGISTRY.get(stream_id)
                if record and record.ref_count <= 0:
                    record.ref_count = 1
            else:
                REGISTRY.mark_reused(record)
                reused = True
        else:
            if record:
                await _stop_ffmpeg_unlocked(stream_id)
            async with _START_SEM:
                ok = await _start_ffmpeg(stream_id, force_sub=force_sub)
            if not ok:
                return SubscribeResult(
                    ok=False,
                    error="No RTSP URL or camera offline",
                )
            record = REGISTRY.get(stream_id)
            if record and record.ref_count <= 0:
                record.ref_count = 1

    if wait_ready:
        ready = await wait_for_playlist(stream_id, timeout=READY_TIMEOUT)
        record = REGISTRY.get(stream_id)
        if record and ready:
            REGISTRY.log_playlist_ready(record)
        if not ready:
            if reused:
                return SubscribeResult(
                    ok=False,
                    reused=True,
                    error="Playlist not ready",
                )
            logging.warning(f"[HLS] Playlist slow for {stream_id}")
    return SubscribeResult(ok=True, reused=reused)


async def batch_subscribe(
    camera_ids: List[str],
    *,
    profile: str = "grid",
) -> Dict[str, Any]:
    """Start grid cameras in small batches to avoid CPU/network spikes."""
    profile = (profile or "grid").strip().lower()
    if profile != "grid":
        logging.warning(f"[HLS] Batch profile '{profile}' not supported; using grid")

    ids = [
        str(c).strip()
        for c in camera_ids
        if c and not str(c).endswith(FULLSCREEN_SUFFIX)
    ]

    results: List[Dict[str, Any]] = []
    started = reused = failed = 0

    async def _subscribe_one(camera_id: str) -> Dict[str, Any]:
        nonlocal started, reused, failed
        playlist = _playlist_url(camera_id)
        try:
            outcome = await subscribe(camera_id, wait_ready=False)
        except Exception as exc:
            failed += 1
            logging.error(f"[HLS] Batch subscribe error {camera_id}: {exc}")
            return {
                "cameraId": camera_id,
                "status": "failed",
                "playlistUrl": playlist,
                "error": str(exc),
            }

        if not outcome.ok:
            failed += 1
            return {
                "cameraId": camera_id,
                "status": "failed",
                "playlistUrl": playlist,
                "error": outcome.error or "Failed to start stream",
            }

        if outcome.reused:
            reused += 1
            status = "reused"
        else:
            started += 1
            status = "started"

        return {
            "cameraId": camera_id,
            "status": status,
            "playlistUrl": playlist,
            "error": None,
        }

    for offset in range(0, len(ids), BATCH_SIZE):
        batch = ids[offset : offset + BATCH_SIZE]
        batch_num = offset // BATCH_SIZE + 1
        logging.info(
            f"[HLS] Batch {batch_num}: starting {len(batch)} camera(s) "
            f"({offset + 1}-{offset + len(batch)} of {len(ids)})"
        )
        batch_outcomes = await asyncio.gather(
            *[_subscribe_one(camera_id) for camera_id in batch],
            return_exceptions=True,
        )
        for camera_id, item in zip(batch, batch_outcomes):
            if isinstance(item, Exception):
                failed += 1
                results.append(
                    {
                        "cameraId": camera_id,
                        "status": "failed",
                        "playlistUrl": _playlist_url(camera_id),
                        "error": str(item),
                    }
                )
            else:
                results.append(item)

        if offset + BATCH_SIZE < len(ids):
            await asyncio.sleep(BATCH_DELAY_SEC)

    summary = {
        "total": len(ids),
        "started": started,
        "reused": reused,
        "failed": failed,
        "results": results,
    }
    logging.info(
        f"[HLS] Batch complete total={summary['total']} "
        f"started={started} reused={reused} failed={failed}"
    )
    return summary


async def unsubscribe(stream_id: str):
    async with REGISTRY.lock(stream_id):
        record = REGISTRY.get(stream_id)
        if not record:
            return
        should_warm = REGISTRY.release_ref(record)
        if should_warm:
            REGISTRY.schedule_warm_stop(record, _stop_stream)


async def is_playlist_ready(stream_id: str) -> bool:
    playlist = _playlist_path(stream_id)
    if not playlist.exists():
        return False
    try:
        text = playlist.read_text(encoding="utf-8", errors="ignore")
        return "#EXTM3U" in text and ".ts" in text
    except OSError:
        return False


async def get_playlist_path(stream_id: str) -> Optional[Path]:
    playlist = _playlist_path(stream_id)
    return playlist if playlist.exists() else None


async def get_stream_status(stream_id: str) -> dict:
    """Return live stream metadata for UI (grid vs fullscreen status badges)."""
    record = REGISTRY.get(stream_id)
    ready = await is_playlist_ready(stream_id)
    label = (record.stream_label if record else "") or ""
    is_fs = _is_fullscreen_stream(stream_id)
    on_sub = record.force_sub if record else False
    if not on_sub and label.startswith("sub/102"):
        on_sub = True
    fallback = is_fs and on_sub
    return {
        "streamId": stream_id,
        "active": bool(record and record.is_process_alive()),
        "ready": ready,
        "streamLabel": label or None,
        "fallback": fallback,
        "lastError": record.last_error if record else None,
        "refCount": record.ref_count if record else 0,
    }


async def _camera_names(camera_ids: List[str]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for camera_id in camera_ids:
        try:
            cam = await camera_collection.find_one(
                {"_id": ObjectId(camera_id)},
                {"name": 1},
            )
            names[camera_id] = (cam.get("name") if cam else None) or camera_id
        except Exception:
            names[camera_id] = camera_id
    return names


async def get_live_diagnostics() -> dict:
    """Admin/debug snapshot of all registry streams."""
    records = REGISTRY.all_records()
    camera_ids = list({_base_camera_id(r.stream_id) for r in records})
    names = await _camera_names(camera_ids)
    frontend_by_stream = FRONTEND_TELEMETRY.all_for_streams(
        [r.stream_id for r in records]
    )

    streams: List[dict] = []
    ffmpeg_count = 0
    seg_cfg = float(HLS_SEGMENT_SECONDS)
    list_cfg = int(HLS_LIST_SIZE)

    for record in sorted(records, key=lambda r: r.stream_id):
        alive = record.is_process_alive()
        if alive:
            ffmpeg_count += 1

        stream_id = record.stream_id
        camera_id = _base_camera_id(stream_id)
        profile = "fullscreen" if _is_fullscreen_stream(stream_id) else "grid"
        ready = await is_playlist_ready(stream_id)
        playlist_path = _playlist_path(stream_id)
        frontend_snap = frontend_by_stream.get(stream_id)

        started_at = None
        if record.started_at_wall is not None:
            started_at = datetime.fromtimestamp(
                record.started_at_wall,
                tz=timezone.utc,
            ).isoformat()

        latency = build_stream_latency(
            record,
            playlist_path,
            profile=profile,
            segment_seconds_configured=seg_cfg,
            list_size_configured=list_cfg,
            frontend=frontend_snap,
        )

        streams.append(
            {
                "cameraId": camera_id,
                "cameraName": names.get(camera_id, camera_id),
                "streamId": stream_id,
                "profile": profile,
                "ffmpegPid": record.proc.pid if alive and record.proc else None,
                "refCount": record.ref_count,
                "playlistReady": ready,
                "lastError": record.last_error,
                "startedAt": started_at,
                "startupMs": record.startup_ms,
                "status": record.status.value,
                "streamLabel": record.stream_label or None,
                "latency": latency,
            }
        )

    active_count = sum(
        1
        for r in records
        if r.ref_count > 0
        or r.status
        in (StreamStatus.RUNNING, StreamStatus.WARMING, StreamStatus.STARTING)
    )

    return {
        "activeStreamCount": active_count,
        "ffmpegProcessCount": ffmpeg_count,
        "hlsConfig": {
            "segmentSeconds": seg_cfg,
            "listSize": list_cfg,
            "flags": HLS_FLAGS,
        },
        "streams": streams,
        **build_ffmpeg_diagnostics_extra(),
    }


async def cleanup_all():
    logging.info("[HLS] Stopping all live streams...")
    await REGISTRY.cleanup_all(kill_process=_kill_process)
    logging.info("[HLS] All live streams stopped.")
