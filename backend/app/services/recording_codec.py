"""Recording source codec detection and FFmpeg encode-mode selection.

H.264/H.265 → stream-copy into HLS/MPEG-TS (no re-encode).
MJPEG (and similar JPEG frame codecs) → encode to H.264 for playable HLS/TS archives.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from app.services.ffmpeg_util import ffprobe_bin

logger = logging.getLogger(__name__)

# Codecs that remux cleanly into HLS MPEG-TS without re-encode.
COPYABLE_VIDEO_CODECS = frozenset(
    {
        "h264",
        "avc1",
        "hevc",
        "h265",
        "hev1",
    }
)

# JPEG-family codecs that are not suitable for direct HLS/TS remux.
MJPEG_VIDEO_CODECS = frozenset(
    {
        "mjpeg",
        "mjpegb",
        "jpeg",
        "ljpeg",
        "jpegls",
    }
)

VIDEO_MODE_COPY = "copy"
VIDEO_MODE_ENCODE_H264 = "encode_h264"


def normalize_video_codec(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def classify_recording_video_mode(codec_name: Optional[str]) -> str:
    """Return VIDEO_MODE_COPY or VIDEO_MODE_ENCODE_H264 from a codec name."""
    codec = normalize_video_codec(codec_name)
    if codec in COPYABLE_VIDEO_CODECS:
        return VIDEO_MODE_COPY
    if codec in MJPEG_VIDEO_CODECS or codec.startswith("mjpeg"):
        return VIDEO_MODE_ENCODE_H264
    # Unknown: prefer copy when possible; FFmpeg may still fail and recovery will surface it.
    if codec in ("", "unknown", "none"):
        return VIDEO_MODE_COPY
    # Conservative: non-copyable JPEG-like or raw → encode for archive playability.
    if "jpeg" in codec or codec in ("rawvideo", "png", "bmp"):
        return VIDEO_MODE_ENCODE_H264
    return VIDEO_MODE_COPY


def input_is_rtsp(url: str) -> bool:
    return (url or "").strip().lower().startswith("rtsp://")


async def probe_recording_video_codec(
    url: str,
    *,
    timeout_sec: float = 12.0,
) -> dict[str, Any]:
    """ffprobe the first video stream. Safe for RTSP and local files."""
    src = (url or "").strip()
    if not src:
        return {
            "ok": False,
            "codec_name": None,
            "video_mode": VIDEO_MODE_COPY,
            "error": "empty_url",
        }

    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,codec_type",
        "-of",
        "json",
    ]
    if input_is_rtsp(src):
        cmd.extend(["-rtsp_transport", "tcp"])
    cmd.append(src)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "ok": False,
                "codec_name": None,
                "video_mode": VIDEO_MODE_COPY,
                "error": "ffprobe_timeout",
            }
    except FileNotFoundError:
        return {
            "ok": False,
            "codec_name": None,
            "video_mode": VIDEO_MODE_COPY,
            "error": "ffprobe_missing",
        }
    except Exception as exc:
        return {
            "ok": False,
            "codec_name": None,
            "video_mode": VIDEO_MODE_COPY,
            "error": str(exc),
        }

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="ignore").strip()[:300]
        logger.warning("[RECORDING] ffprobe failed for recording source: %s", err or proc.returncode)
        return {
            "ok": False,
            "codec_name": None,
            "video_mode": VIDEO_MODE_COPY,
            "error": err or f"exit_{proc.returncode}",
        }

    try:
        data = json.loads((stdout or b"{}").decode("utf-8", errors="ignore") or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "codec_name": None,
            "video_mode": VIDEO_MODE_COPY,
            "error": "ffprobe_bad_json",
        }

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video and streams:
        video = streams[0]
    codec = normalize_video_codec((video or {}).get("codec_name"))
    mode = classify_recording_video_mode(codec)
    return {
        "ok": True,
        "codec_name": codec or None,
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "video_mode": mode,
        "error": None,
    }


def video_encode_args_for_mode(video_mode: str) -> list[str]:
    """FFmpeg -c:v … args after -map 0:v:0."""
    if video_mode == VIDEO_MODE_ENCODE_H264:
        # MJPEG → H.264 for HLS/TS Playback + export compatibility.
        return [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "main",
        ]
    return ["-c:v", "copy"]
