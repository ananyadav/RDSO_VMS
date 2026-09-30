"""Freeze Instant Replay rolling-buffer footage into a durable pre-alarm clip.

Copies the last N seconds of real HLS segments from the live IR buffer into a
destination directory and writes a closed playlist. Does not start a new FFmpeg
producer and does not enable fleet recording.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.services.instant_replay_buffer import _BUFFERS, _segment_bounds, buffer_dir
from app.services.instant_replay_config import (
    INSTANT_REPLAY_BUFFER_SECONDS,
    INSTANT_REPLAY_ENABLED,
    INSTANT_REPLAY_SEGMENT_SECONDS,
)

logger = logging.getLogger(__name__)


def max_pre_alarm_seconds() -> int:
    return int(INSTANT_REPLAY_BUFFER_SECONDS)


def snapshot_instant_replay_pre_alarm(
    camera_uid: str,
    *,
    pre_alarm_seconds: int,
    dest_dir: Path,
    event_time: Optional[datetime] = None,
) -> dict[str, Any]:
    """
    Copy real buffer segments covering [event_time - pre, event_time] (approx)
    into dest_dir. Returns status: ok | partial | unavailable.
    """
    uid = (camera_uid or "").strip()
    seconds = max(0, int(pre_alarm_seconds))
    if seconds <= 0:
        return {
            "status": "skipped",
            "source": None,
            "seconds_requested": 0,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    if not INSTANT_REPLAY_ENABLED:
        return {
            "status": "unavailable",
            "source": None,
            "error": "Instant Replay buffer is disabled",
            "seconds_requested": seconds,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    state = _BUFFERS.get(uid)
    src_dir = Path(state["dir"]) if state and state.get("dir") else buffer_dir(uid)
    if not src_dir.is_dir():
        return {
            "status": "unavailable",
            "source": "instant_replay_buffer",
            "error": "No Instant Replay buffer directory",
            "seconds_requested": seconds,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    segs = sorted(src_dir.glob("seg_*.ts"))
    if not segs:
        segs = sorted(src_dir.glob("*.ts"))
    usable = [s for s in segs if s.is_file() and s.stat().st_size > 0]
    if not usable:
        return {
            "status": "unavailable",
            "source": "instant_replay_buffer",
            "error": "Instant Replay buffer has no segments yet",
            "seconds_requested": seconds,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    end_t = event_time or datetime.now(timezone.utc)
    if end_t.tzinfo is None:
        end_t = end_t.replace(tzinfo=timezone.utc)
    start_t = end_t - timedelta(seconds=seconds)
    seg_len = max(1, int(INSTANT_REPLAY_SEGMENT_SECONDS))

    # Include segments whose mtime falls in [start_t - seg_len, end_t + seg_len]
    # so we capture real footage that overlaps the pre-alarm window.
    selected: list[Path] = []
    for seg in usable:
        mtime = datetime.fromtimestamp(seg.stat().st_mtime, tz=timezone.utc)
        if mtime >= start_t - timedelta(seconds=seg_len) and mtime <= end_t + timedelta(seconds=seg_len):
            selected.append(seg)

    # If none matched the window (clock skew), take the newest N segments by count.
    if not selected:
        need = max(1, int(seconds / seg_len) + 1)
        selected = usable[-need:]

    dest_dir.mkdir(parents=True, exist_ok=True)
    # Clear prior snapshot
    for old in dest_dir.glob("*"):
        try:
            if old.is_file():
                old.unlink()
        except OSError:
            pass

    copied: list[str] = []
    for idx, seg in enumerate(selected):
        name = f"seg_{idx:05d}.ts"
        try:
            shutil.copy2(seg, dest_dir / name)
            copied.append(name)
        except OSError as exc:
            logger.warning("[pre-alarm] Failed to copy %s: %s", seg, exc)

    if not copied:
        return {
            "status": "unavailable",
            "source": "instant_replay_buffer",
            "error": "Failed to copy buffer segments",
            "seconds_requested": seconds,
            "seconds_captured": 0,
            "segment_count": 0,
        }

    playlist = dest_dir / "index.m3u8"
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{seg_len}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
    ]
    for name in copied:
        lines.append(f"#EXTINF:{seg_len:.3f},")
        lines.append(name)
    lines.append("#EXT-X-ENDLIST")
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    captured = len(copied) * seg_len
    oldest, newest, _ = _segment_bounds(dest_dir)
    status = "ok" if captured >= max(1, seconds - seg_len) else "partial"
    return {
        "status": status,
        "source": "instant_replay_buffer",
        "seconds_requested": seconds,
        "seconds_captured": min(captured, seconds),
        "segment_count": len(copied),
        "path": str(dest_dir),
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
    }
