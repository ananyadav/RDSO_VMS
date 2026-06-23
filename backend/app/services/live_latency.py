"""
Live stream latency diagnostics — backend milestones + frontend telemetry merge.

Measurement only; does not change segmenting or buffering behavior.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.live_stream_registry import StreamRecord

_TELEMETRY_TTL_SEC = 120.0


def _iso_wall(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _delta_ms(start: Optional[float], end: Optional[float]) -> Optional[int]:
    if start is None or end is None:
        return None
    return max(0, int((end - start) * 1000))


@dataclass
class FrontendLatencySnapshot:
    stream_id: str
    profile: str
    acquire_wall: Optional[float] = None
    manifest_loaded_wall: Optional[float] = None
    video_playing_wall: Optional[float] = None
    live_edge_delay_sec: Optional[float] = None
    buffer_length_sec: Optional[float] = None
    updated_at_wall: float = field(default_factory=time.time)

    def startup_latency_ms(self) -> Optional[int]:
        return _delta_ms(self.acquire_wall, self.video_playing_wall)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "acquireAt": _iso_wall(self.acquire_wall),
            "manifestLoadedAt": _iso_wall(self.manifest_loaded_wall),
            "videoPlayingAt": _iso_wall(self.video_playing_wall),
            "startupLatencyMs": self.startup_latency_ms(),
            "liveEdgeDelaySec": self.live_edge_delay_sec,
            "bufferLengthSec": self.buffer_length_sec,
            "updatedAt": _iso_wall(self.updated_at_wall),
        }


class FrontendTelemetryStore:
    """In-memory latest frontend metrics per stream id (diagnostics only)."""

    def __init__(self) -> None:
        self._snapshots: Dict[str, FrontendLatencySnapshot] = {}

    def _prune(self) -> None:
        now = time.time()
        stale = [
            sid
            for sid, snap in self._snapshots.items()
            if now - snap.updated_at_wall > _TELEMETRY_TTL_SEC
        ]
        for sid in stale:
            self._snapshots.pop(sid, None)

    def update(self, payload: dict) -> None:
        self._prune()
        stream_id = str(payload.get("streamId") or "").strip()
        if not stream_id:
            return

        profile = str(payload.get("profile") or "grid").strip().lower()
        snap = self._snapshots.get(stream_id)
        if snap is None:
            snap = FrontendLatencySnapshot(stream_id=stream_id, profile=profile)
            self._snapshots[stream_id] = snap

        snap.profile = profile
        snap.updated_at_wall = time.time()

        for key, attr in (
            ("acquireWall", "acquire_wall"),
            ("manifestLoadedWall", "manifest_loaded_wall"),
            ("videoPlayingWall", "video_playing_wall"),
        ):
            val = payload.get(key)
            if val is not None:
                try:
                    setattr(snap, attr, float(val))
                except (TypeError, ValueError):
                    pass

        for key, attr in (
            ("liveEdgeDelaySec", "live_edge_delay_sec"),
            ("bufferLengthSec", "buffer_length_sec"),
        ):
            val = payload.get(key)
            if val is not None:
                try:
                    setattr(snap, attr, float(val))
                except (TypeError, ValueError):
                    pass

    def get(self, stream_id: str) -> Optional[FrontendLatencySnapshot]:
        self._prune()
        return self._snapshots.get(stream_id)

    def all_for_streams(self, stream_ids: List[str]) -> Dict[str, FrontendLatencySnapshot]:
        self._prune()
        return {sid: self._snapshots[sid] for sid in stream_ids if sid in self._snapshots}


FRONTEND_TELEMETRY = FrontendTelemetryStore()


def parse_playlist_meta(
    playlist_path: Path,
    *,
    segment_seconds_configured: float,
    list_size_configured: int,
) -> dict:
    segment_duration: Optional[float] = None
    target_duration: Optional[float] = None
    segment_count = 0

    if playlist_path.exists():
        try:
            text = playlist_path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.startswith("#EXT-X-TARGETDURATION:"):
                    try:
                        target_duration = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("#EXTINF:"):
                    segment_count += 1
                    if segment_duration is None:
                        try:
                            segment_duration = float(line.split(":", 1)[1].split(",")[0])
                        except ValueError:
                            pass
        except OSError:
            pass

    return {
        "hlsSegmentDurationSec": segment_duration,
        "hlsTargetDurationSec": target_duration,
        "hlsPlaylistSegmentCount": segment_count,
        "hlsListSizeConfigured": list_size_configured,
        "hlsSegmentSecondsConfigured": segment_seconds_configured,
    }


def mark_rtsp_connected(record: StreamRecord) -> None:
    if record.rtsp_connected_wall is not None:
        return
    record.rtsp_connected_wall = time.time()
    ms = _delta_ms(record.started_at_wall, record.rtsp_connected_wall)
    logging.info(
        "[HLS][latency] RTSP connected streamId=%s msSinceFfmpegStart=%s",
        record.stream_id,
        ms,
    )


def mark_playlist_created(record: StreamRecord) -> None:
    if record.playlist_created_wall is not None:
        return
    record.playlist_created_wall = time.time()
    ms = _delta_ms(record.started_at_wall, record.playlist_created_wall)
    logging.info(
        "[HLS][latency] playlist created streamId=%s msSinceFfmpegStart=%s",
        record.stream_id,
        ms,
    )


def mark_first_segment_created(record: StreamRecord) -> None:
    if record.first_segment_created_wall is not None:
        return
    record.first_segment_created_wall = time.time()
    ms = _delta_ms(record.started_at_wall, record.first_segment_created_wall)
    logging.info(
        "[HLS][latency] first segment created streamId=%s msSinceFfmpegStart=%s",
        record.stream_id,
        ms,
    )


def mark_playlist_ready(record: StreamRecord) -> int:
    """Record playlist-ready wall time; returns elapsed ms from ffmpeg start."""
    now = time.time()
    if record.playlist_ready_wall is None:
        record.playlist_ready_wall = now
    if record.started_at is not None:
        elapsed_ms = int((time.monotonic() - record.started_at) * 1000)
    else:
        elapsed_ms = _delta_ms(record.started_at_wall, record.playlist_ready_wall) or 0
    record.startup_ms = elapsed_ms
    logging.info(
        "[HLS][latency] playlist ready streamId=%s readyMs=%s",
        record.stream_id,
        elapsed_ms,
    )
    return elapsed_ms


def is_rtsp_connected_stderr(line: str) -> bool:
    lower = line.lower()
    return "rtsp" in lower and "opening" in lower


def build_stream_latency(
    record: StreamRecord,
    playlist_path: Path,
    *,
    profile: str,
    segment_seconds_configured: float,
    list_size_configured: int,
    frontend: Optional[FrontendLatencySnapshot] = None,
) -> dict:
    ffmpeg_start = record.started_at_wall
    playlist_ready = record.playlist_ready_wall
    backend_startup_ms = _delta_ms(ffmpeg_start, playlist_ready)
    if backend_startup_ms is None and record.startup_ms is not None:
        backend_startup_ms = record.startup_ms

    first_segment_ms = _delta_ms(ffmpeg_start, record.first_segment_created_wall)
    rtsp_ms = _delta_ms(ffmpeg_start, record.rtsp_connected_wall)
    playlist_created_ms = _delta_ms(ffmpeg_start, record.playlist_created_wall)

    frontend_dict: Optional[dict] = frontend.to_dict() if frontend else None
    approx_total_startup_ms: Optional[int] = None
    if ffmpeg_start is not None and frontend and frontend.video_playing_wall:
        approx_total_startup_ms = _delta_ms(ffmpeg_start, frontend.video_playing_wall)

    return {
        "profile": profile,
        "ffmpegStartAt": _iso_wall(ffmpeg_start),
        "rtspConnectedAt": _iso_wall(record.rtsp_connected_wall),
        "playlistCreatedAt": _iso_wall(record.playlist_created_wall),
        "firstSegmentCreatedAt": _iso_wall(record.first_segment_created_wall),
        "playlistReadyAt": _iso_wall(playlist_ready),
        "rtspConnectMs": rtsp_ms,
        "playlistCreatedMs": playlist_created_ms,
        "firstSegmentMs": first_segment_ms,
        "backendStartupMs": backend_startup_ms,
        "approxTotalStartupMs": approx_total_startup_ms,
        **parse_playlist_meta(
            playlist_path,
            segment_seconds_configured=segment_seconds_configured,
            list_size_configured=list_size_configured,
        ),
        "frontend": frontend_dict,
    }


def ingest_telemetry_payload(body: dict) -> dict:
    """Validate and store a frontend telemetry POST."""
    stream_id = str(body.get("streamId") or "").strip()
    if not stream_id:
        return {"ok": False, "error": "streamId required"}

    FRONTEND_TELEMETRY.update(body)
    snap = FRONTEND_TELEMETRY.get(stream_id)
    return {
        "ok": True,
        "streamId": stream_id,
        "snapshot": snap.to_dict() if snap else None,
    }
