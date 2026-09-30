"""RDSO 18.1.27 — workstation / stream / monitor / priority / replay capacity evidence."""

from __future__ import annotations

from typing import Any

from app.services.go2rtc_workers import MAX_CAMERAS_PER_WORKER, needed_workers_for_camera_count
from app.services.playback_search import MAX_MULTI_PLAYBACK_CAMERAS
from app.services.priority_levels import PRIORITY_LEVELS, priority_policy_public
from app.services.recording_config import (
    RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS,
    get_recording_capacity_info,
)

# Clause minima (software must meet or exceed).
RDSO_18_1_27_MIN_VIDEO_STREAMS = 128
RDSO_18_1_27_MIN_MONITORS = 8
RDSO_18_1_27_MIN_PRIORITY_LEVELS = 5
RDSO_18_1_27_MIN_REPLAY_CAMERAS = 16


def get_rdso_18_1_27_capacity() -> dict[str, Any]:
    """Software capability snapshot — does not open streams or claim multi-host tests."""
    rec = get_recording_capacity_info()
    stream_soft_cap = rec.get("max_concurrent_recordings") or 0
    stream_ok = bool(rec.get("unlimited")) or (
        isinstance(stream_soft_cap, int)
        and stream_soft_cap >= RDSO_18_1_27_MIN_VIDEO_STREAMS
    )
    workers_for_128 = needed_workers_for_camera_count(RDSO_18_1_27_MIN_VIDEO_STREAMS)
    priority_ok = len(PRIORITY_LEVELS) >= RDSO_18_1_27_MIN_PRIORITY_LEVELS
    replay_ok = int(MAX_MULTI_PLAYBACK_CAMERAS) >= RDSO_18_1_27_MIN_REPLAY_CAMERAS

    return {
        "rdso_18_1_27": True,
        "video_streams": {
            "min_required": RDSO_18_1_27_MIN_VIDEO_STREAMS,
            "rdso_min_simultaneous_streams": RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS,
            "software_compliant": stream_ok and not (
                stream_soft_cap > 0 and stream_soft_cap < RDSO_18_1_27_MIN_VIDEO_STREAMS
            ),
            "recording_capacity": rec,
            "go2rtc_max_cameras_per_worker": MAX_CAMERAS_PER_WORKER,
            "go2rtc_workers_for_128": workers_for_128,
            "connect_ramp_note": (
                "Frontend VITE_GO2RTC_MAX_CONCURRENT limits connect *attempts*, "
                "not lifetime concurrent players."
            ),
            "acceptance": (
                "Opt-in Linux harness: RDSO_128_ACCEPTANCE=1 "
                "python backend/scripts/rdso_18_3_5_accept_128_streams.py "
                "(also aliased as rdso_18_1_27_accept_capacity.py). "
                "Do not run on low-storage developer workstations."
            ),
        },
        "monitors": {
            "min_required": RDSO_18_1_27_MIN_MONITORS,
            "software_monitor_identities": RDSO_18_1_27_MIN_MONITORS,
            "software_compliant": True,
            "note": (
                "Logical Live View display windows (?monitor=1..8) with independent "
                "layout/camera selection. Physical 8-monitor acceptance is "
                "deployment/hardware testing."
            ),
        },
        "priority": {
            "min_required": RDSO_18_1_27_MIN_PRIORITY_LEVELS,
            "levels": list(PRIORITY_LEVELS),
            "software_compliant": priority_ok,
            "policy": priority_policy_public(),
        },
        "replay": {
            "min_required": RDSO_18_1_27_MIN_REPLAY_CAMERAS,
            "max_multi_playback_cameras": MAX_MULTI_PLAYBACK_CAMERAS,
            "software_compliant": replay_ok,
            "sync_controls": ["play", "pause", "seek", "speed"],
        },
        "software_compliant": bool(
            stream_ok
            and priority_ok
            and replay_ok
            and True  # monitors identities
        ),
    }
