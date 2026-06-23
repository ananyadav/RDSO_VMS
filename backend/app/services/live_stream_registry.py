"""
Live stream registry — one FFmpeg process per stream id (grid camera or fullscreen).

Tracks: process, status, playlist path, ref count, started time, last error.
Per-stream asyncio locks prevent duplicate FFmpeg starts for the same camera.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Awaitable, Dict, List, Optional

WARM_SECONDS = float(
    os.getenv("HLS_KEEP_WARM_SECONDS") or os.getenv("HLS_WARM_SECONDS") or "30"
)


class StreamStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    WARMING = "warming"
    STOPPED = "stopped"


@dataclass
class StreamRecord:
    stream_id: str
    playlist_path: Path
    proc: Optional[asyncio.subprocess.Process] = None
    status: StreamStatus = StreamStatus.STOPPED
    ref_count: int = 0
    started_at: Optional[float] = None
    started_at_wall: Optional[float] = None
    startup_ms: Optional[int] = None
    rtsp_connected_wall: Optional[float] = None
    playlist_created_wall: Optional[float] = None
    first_segment_created_wall: Optional[float] = None
    playlist_ready_wall: Optional[float] = None
    last_error: Optional[str] = None
    monitor_task: Optional[asyncio.Task] = None
    latency_watch_task: Optional[asyncio.Task] = None
    warm_stop_task: Optional[asyncio.Task] = None
    # FFmpeg profile (restart / fallback logic in video_live_hls)
    use_preview: bool = False
    use_main: bool = False
    force_sub: bool = False
    stream_label: str = ""

    def is_process_alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def should_keep_ffmpeg(self) -> bool:
        return self.ref_count > 0 or self.status == StreamStatus.WARMING


StopCallback = Callable[[str], Awaitable[None]]


class LiveStreamRegistry:
    def __init__(self) -> None:
        self._streams: Dict[str, StreamRecord] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock(self, stream_id: str) -> asyncio.Lock:
        if stream_id not in self._locks:
            self._locks[stream_id] = asyncio.Lock()
        return self._locks[stream_id]

    def get(self, stream_id: str) -> Optional[StreamRecord]:
        return self._streams.get(stream_id)

    def all_stream_ids(self) -> list[str]:
        return list(self._streams.keys())

    def all_records(self) -> List[StreamRecord]:
        return list(self._streams.values())

    def ensure_record(self, stream_id: str, playlist_path: Path) -> StreamRecord:
        rec = self._streams.get(stream_id)
        if rec is None:
            rec = StreamRecord(stream_id=stream_id, playlist_path=playlist_path)
            self._streams[stream_id] = rec
        else:
            rec.playlist_path = playlist_path
        return rec

    def cancel_warm_stop(self, record: StreamRecord) -> None:
        task = record.warm_stop_task
        if task and not task.done():
            task.cancel()
        record.warm_stop_task = None

    def tear_down_process(self, record: StreamRecord) -> None:
        """Kill FFmpeg tasks but keep registry entry (ref count preserved for restart)."""
        self.cancel_warm_stop(record)
        if record.monitor_task and not record.monitor_task.done():
            record.monitor_task.cancel()
        record.monitor_task = None
        if record.latency_watch_task and not record.latency_watch_task.done():
            record.latency_watch_task.cancel()
        record.latency_watch_task = None
        record.proc = None

    def remove_record(self, stream_id: str) -> None:
        self._streams.pop(stream_id, None)
        self._locks.pop(stream_id, None)

    def mark_reused(self, record: StreamRecord) -> None:
        if record.status == StreamStatus.WARMING:
            self.cancel_warm_stop(record)
            record.status = StreamStatus.RUNNING
        record.ref_count += 1
        pid = record.proc.pid if record.proc else None
        logging.info(
            f"[HLS][registry] stream reused streamId={record.stream_id} "
            f"refCount={record.ref_count} pid={pid}"
        )

    def mark_started(
        self,
        record: StreamRecord,
        proc: asyncio.subprocess.Process,
        *,
        use_preview: bool,
        use_main: bool,
        force_sub: bool,
        stream_label: str,
        monitor_task: asyncio.Task,
    ) -> None:
        self.cancel_warm_stop(record)
        record.proc = proc
        record.status = StreamStatus.RUNNING
        if record.latency_watch_task and not record.latency_watch_task.done():
            record.latency_watch_task.cancel()
        record.latency_watch_task = None
        record.started_at = time.monotonic()
        record.started_at_wall = time.time()
        record.startup_ms = None
        record.rtsp_connected_wall = None
        record.playlist_created_wall = None
        record.first_segment_created_wall = None
        record.playlist_ready_wall = None
        record.last_error = None
        record.use_preview = use_preview
        record.use_main = use_main
        record.force_sub = force_sub
        record.stream_label = stream_label
        record.monitor_task = monitor_task
        if record.ref_count <= 0:
            record.ref_count = 1
        logging.info(
            f"[HLS][registry] stream started streamId={record.stream_id} "
            f"ffmpeg pid={proc.pid} refCount={record.ref_count}"
        )

    def log_playlist_ready(self, record: StreamRecord) -> None:
        from app.services.live_latency import mark_playlist_ready

        mark_playlist_ready(record)

    def set_error(self, record: StreamRecord, message: str) -> None:
        record.last_error = message
        logging.warning(
            f"[HLS][registry] stream error streamId={record.stream_id} "
            f"error={message}"
        )

    def release_ref(self, record: StreamRecord) -> bool:
        """Decrement ref count. Returns True if warm stop should be scheduled."""
        record.ref_count = max(0, record.ref_count - 1)
        logging.info(
            f"[HLS][registry] stream release streamId={record.stream_id} "
            f"refCount={record.ref_count}"
        )
        return record.ref_count <= 0 and record.is_process_alive()

    def schedule_warm_stop(
        self,
        record: StreamRecord,
        stop_callback: StopCallback,
    ) -> None:
        self.cancel_warm_stop(record)
        record.status = StreamStatus.WARMING
        stream_id = record.stream_id
        logging.info(
            f"[HLS][registry] stream warming streamId={stream_id} "
            f"warmSeconds={WARM_SECONDS}"
        )

        async def _warm_stop() -> None:
            try:
                await asyncio.sleep(WARM_SECONDS)
                async with self.lock(stream_id):
                    current = self._streams.get(stream_id)
                    if not current or current is not record:
                        return
                    if current.ref_count > 0:
                        return
                    await stop_callback(stream_id)
            except asyncio.CancelledError:
                pass

        record.warm_stop_task = asyncio.create_task(_warm_stop())

    async def stop_and_remove(
        self,
        stream_id: str,
        *,
        kill_process: Callable[[StreamRecord], Awaitable[None]],
    ) -> None:
        async with self.lock(stream_id):
            record = self._streams.pop(stream_id, None)
            if not record:
                return
            self.cancel_warm_stop(record)
            if record.monitor_task and not record.monitor_task.done():
                record.monitor_task.cancel()
            if record.latency_watch_task and not record.latency_watch_task.done():
                record.latency_watch_task.cancel()
            pid = record.proc.pid if record.proc else None
            await kill_process(record)
            record.status = StreamStatus.STOPPED
            logging.info(
                f"[HLS][registry] stream stopped streamId={stream_id} pid={pid}"
            )
        self._locks.pop(stream_id, None)

    async def cleanup_all(
        self,
        *,
        kill_process: Callable[[StreamRecord], Awaitable[None]],
    ) -> None:
        for stream_id in list(self._streams.keys()):
            await self.stop_and_remove(stream_id, kill_process=kill_process)


REGISTRY = LiveStreamRegistry()
