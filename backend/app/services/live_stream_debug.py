"""
Fullscreen live stream verification — diagnostics only (no streaming changes).

On each fullscreen POST /api/live/{cameraId}__fullscreen/start:
  - log RTSP URL + channel
  - ffprobe the incoming stream
  - store resolution / codec / fps / bitrate for GET /api/live/debug/{cameraId}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.ffmpeg_util import ffprobe_bin
from app.services.live_stream_registry import REGISTRY
from app.services.rtsp_utils import mask_rtsp_url
from app.services.video_live_hls import (
    FULLSCREEN_SUFFIX,
    _get_camera_doc,
    _pick_live_urls,
)

logger = logging.getLogger(__name__)

_PROBE_CACHE_SECONDS = 60.0
_PROBE_453_BACKOFF_SECONDS = 120.0

_CHANNEL_RE = re.compile(r"/Channels/(\d{3})")
_DEBUG_STORE: Dict[str, dict] = {}
_VERIFY_TASKS: Dict[str, asyncio.Task] = {}
_last_probe_at: Dict[str, float] = {}
_probe_backoff_until: Dict[str, float] = {}


def is_fullscreen_stream_id(stream_id: str) -> bool:
    return stream_id.endswith(FULLSCREEN_SUFFIX)


def base_camera_id(stream_id: str) -> str:
    if stream_id.endswith(FULLSCREEN_SUFFIX):
        return stream_id[: -len(FULLSCREEN_SUFFIX)]
    return stream_id


def channel_from_label(label: str) -> Optional[str]:
    """Extract 101 / 102 / 103 from labels like main/101 or sub/102 (no preview)."""
    if not label:
        return None
    slash_match = re.search(r"/(\d{3})", label)
    if slash_match:
        return slash_match.group(1)
    for token in label.replace("(", " ").split():
        if token.isdigit() and len(token) == 3:
            return token
    return None


def channel_from_url(rtsp_url: str) -> Optional[str]:
    match = _CHANNEL_RE.search(rtsp_url or "")
    return match.group(1) if match else None


def _parse_fps(raw: Optional[str]) -> Optional[float]:
    if not raw or raw == "?":
        return None
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            den_f = float(den)
            return round(float(num) / den_f, 2) if den_f else None
        except ValueError:
            return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def probe_rtsp_sync(rtsp_url: str, *, timeout: float = 25.0) -> dict:
    """Run ffprobe on a live RTSP URL (blocking — use via executor)."""
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate",
        "-show_entries",
        "format=bit_rate",
        "-of",
        "json",
        rtsp_url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "ffprobe failed").strip()
            return {"error": err[:400]}
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        bitrate = stream.get("bit_rate") or (data.get("format") or {}).get("bit_rate")
        fps_raw = stream.get("r_frame_rate") or stream.get("avg_frame_rate")
        width = stream.get("width")
        height = stream.get("height")
        return {
            "codec": stream.get("codec_name"),
            "resolution": f"{width}x{height}" if width and height else None,
            "fps": _parse_fps(fps_raw),
            "bitrate_kbps": round(int(bitrate) / 1000) if bitrate else None,
        }
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timeout"}
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def probe_rtsp(rtsp_url: str, *, timeout: float = 25.0) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: probe_rtsp_sync(rtsp_url, timeout=timeout)
    )


def _should_skip_probe(camera_id: str, stream_id: str) -> Optional[str]:
    """Return skip reason if ffprobe should not run (cache / 453 backoff)."""
    now = time.monotonic()
    if now < _probe_backoff_until.get(camera_id, 0):
        return "RTSP 453 backoff active"

    if now - _last_probe_at.get(camera_id, 0) < _PROBE_CACHE_SECONDS:
        if camera_id in _DEBUG_STORE:
            return "probe cache fresh (60s)"

    record = REGISTRY.get(stream_id)
    if record and record.last_error:
        err = record.last_error.lower()
        if "453" in err or "not enough bandwidth" in err:
            return "fullscreen FFmpeg reported RTSP 453"

    if record and not record.is_process_alive() and record.last_error:
        err = record.last_error.lower()
        if "453" in err:
            return "fullscreen FFmpeg exited with RTSP 453"

    return None


def _note_probe_result(camera_id: str, probe: dict) -> None:
    _last_probe_at[camera_id] = time.monotonic()
    err = (probe.get("error") or "").lower()
    if "453" in err or "not enough bandwidth" in err:
        _probe_backoff_until[camera_id] = (
            time.monotonic() + _PROBE_453_BACKOFF_SECONDS
        )


def _api_payload(stored: dict) -> dict:
    """Minimal response for GET /api/live/debug/{cameraId}."""
    payload: Dict[str, Any] = {
        "channel": stored.get("channel"),
        "resolution": stored.get("resolution"),
        "codec": stored.get("codec"),
        "fps": stored.get("fps"),
    }
    if stored.get("error") and not stored.get("codec"):
        payload["error"] = stored["error"]
    return payload


async def verify_fullscreen_stream(
    stream_id: str,
    *,
    playlist_already_ready: bool = False,
) -> None:
    """Resolve fullscreen RTSP, log, ffprobe, and store by base camera id."""
    camera_id = base_camera_id(stream_id)

    skip = _should_skip_probe(camera_id, stream_id)
    if skip:
        logger.info(
            "[LIVE-DEBUG] skip probe camera=%s stream=%s reason=%s",
            camera_id,
            stream_id,
            skip,
        )
        return

    cam = await _get_camera_doc(camera_id)
    if not cam:
        _DEBUG_STORE[camera_id] = {
            "camera_id": camera_id,
            "stream_id": stream_id,
            "error": "camera not found",
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }
        return

    record = REGISTRY.get(stream_id)
    force_sub = bool(record and record.force_sub)
    rtsp_url, label = _pick_live_urls(
        cam, stream_id=stream_id, force_sub=force_sub
    )
    if not rtsp_url:
        _DEBUG_STORE[camera_id] = {
            "camera_id": camera_id,
            "stream_id": stream_id,
            "error": "no RTSP URL",
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }
        return

    channel = channel_from_label(label) or channel_from_url(rtsp_url)
    masked = mask_rtsp_url(rtsp_url)

    logger.info(
        "[LIVE-DEBUG] fullscreen stream verify camera=%s stream=%s channel=%s url=%s",
        camera_id,
        stream_id,
        channel or "?",
        masked,
    )

    # Brief delay so FFmpeg RTSP SETUP completes before ffprobe (separate client).
    if not playlist_already_ready:
        await asyncio.sleep(1.5)

    # Re-read registry — fallback to sub/102 may have occurred during startup.
    record = REGISTRY.get(stream_id)
    if record:
        force_sub = bool(record.force_sub)
        label = record.stream_label or label
        if record.force_sub or label.startswith("sub/102"):
            rtsp_url, label = _pick_live_urls(
                cam, stream_id=stream_id, force_sub=True
            )
            channel = channel_from_label(label) or channel_from_url(rtsp_url or "")
            masked = mask_rtsp_url(rtsp_url or "")

    probe = await probe_rtsp(rtsp_url) if rtsp_url else {"error": "no RTSP URL"}
    _note_probe_result(camera_id, probe)

    stored = {
        "camera_id": camera_id,
        "stream_id": stream_id,
        "rtsp_url_masked": masked,
        "stream_label": label,
        "channel": channel,
        "codec": probe.get("codec"),
        "resolution": probe.get("resolution"),
        "fps": probe.get("fps"),
        "bitrate_kbps": probe.get("bitrate_kbps"),
        "error": probe.get("error"),
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }
    _DEBUG_STORE[camera_id] = stored

    logger.info(
        "[LIVE-DEBUG] fullscreen probe camera=%s channel=%s %s %s %sfps bitrate=%s",
        camera_id,
        channel or "?",
        stored.get("resolution") or "—",
        stored.get("codec") or "—",
        stored.get("fps") or "—",
        f"{stored['bitrate_kbps']}kbps" if stored.get("bitrate_kbps") else "n/a",
    )


def schedule_probe_when_playlist_ready(stream_id: str) -> None:
    """Run ffprobe only after fullscreen HLS playlist is ready (on-demand diagnostics)."""
    if not is_fullscreen_stream_id(stream_id):
        return

    prev = _VERIFY_TASKS.get(stream_id)
    if prev and not prev.done():
        return

    async def _run() -> None:
        from app.services.video_live_hls import wait_for_playlist

        camera_id = base_camera_id(stream_id)
        skip = _should_skip_probe(camera_id, stream_id)
        if skip:
            logger.info(
                "[LIVE-DEBUG] skip probe camera=%s reason=%s",
                camera_id,
                skip,
            )
            return
        ready = await wait_for_playlist(stream_id, timeout=30.0)
        if not ready:
            logger.info(
                "[LIVE-DEBUG] skip probe camera=%s — playlist not ready within 30s",
                camera_id,
            )
            return
        await verify_fullscreen_stream(stream_id, playlist_already_ready=True)

    task = asyncio.create_task(_run())

    def _done(t: asyncio.Task) -> None:
        _VERIFY_TASKS.pop(stream_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.warning(
                "[LIVE-DEBUG] probe failed stream=%s: %s",
                stream_id,
                exc,
            )

    task.add_done_callback(_done)
    _VERIFY_TASKS[stream_id] = task


def schedule_fullscreen_verification(stream_id: str) -> None:
    """Legacy alias — probes only after playlist is ready."""
    schedule_probe_when_playlist_ready(stream_id)


async def get_fullscreen_debug(camera_id: str) -> Optional[dict]:
    """Return stored probe snapshot for API (None if never verified)."""
    stored = _DEBUG_STORE.get(camera_id)
    if not stored:
        return None
    return _api_payload(stored)


def clear_debug_store() -> None:
    """Test helper."""
    _DEBUG_STORE.clear()
    _last_probe_at.clear()
    _probe_backoff_until.clear()
    for task in _VERIFY_TASKS.values():
        task.cancel()
    _VERIFY_TASKS.clear()
