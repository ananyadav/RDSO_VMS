"""RDSO 18.3.14 — validate idle vs active stream bitrates/profiles (read-only).

Does not reconfigure camera encoders on motion events. Uses existing stream-profile
reads (ISAPI/ONVIF). Compliance is never claimed from main/sub labels alone.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

COMPLIANT = "compliant"
UNKNOWN = "unknown"
NON_COMPLIANT = "non_compliant"


def _pixels(profile_block: dict | None) -> Optional[int]:
    cur = (profile_block or {}).get("current") or {}
    w, h = cur.get("width"), cur.get("height")
    try:
        if w and h:
            return int(w) * int(h)
    except (TypeError, ValueError):
        return None
    return None


def _bitrate(profile_block: dict | None) -> Optional[int]:
    cur = (profile_block or {}).get("current") or {}
    raw = cur.get("bitrate_kbps")
    if raw is None:
        return None
    try:
        val = int(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def summarize_stream_profile(block: dict | None, *, role: str) -> dict[str, Any]:
    cur = (block or {}).get("current") or {}
    return {
        "role": role,
        "profile": (block or {}).get("profile"),
        "channel": (block or {}).get("channel"),
        "supported": bool((block or {}).get("supported")),
        "bitrate_kbps": _bitrate(block),
        "fps": cur.get("fps"),
        "width": cur.get("width"),
        "height": cur.get("height"),
        "resolution": cur.get("resolution"),
        "codec": cur.get("codec"),
        "rate_control": cur.get("rate_control"),
        "pixels": _pixels(block),
        "message": (block or {}).get("message"),
    }


def compare_idle_active_profiles(
    idle_block: dict | None,
    active_block: dict | None,
) -> dict[str, Any]:
    """Return compliance from measured bitrates (or pixel proxy only as secondary hint)."""
    idle = summarize_stream_profile(idle_block, role="idle")
    active = summarize_stream_profile(active_block, role="active")
    idle_br = idle.get("bitrate_kbps")
    active_br = active.get("bitrate_kbps")

    result: dict[str, Any] = {
        "idle": idle,
        "active": active,
        "idle_bitrate_kbps": idle_br,
        "active_bitrate_kbps": active_br,
        "compliance": UNKNOWN,
        "bitrate_known": idle_br is not None and active_br is not None,
        "message": "",
    }

    if not (idle_block or {}).get("supported") or not (active_block or {}).get("supported"):
        result["compliance"] = UNKNOWN
        result["message"] = (
            "Stream profile unavailable for idle and/or active stream — "
            "cannot claim bitrate compliance"
        )
        return result

    if idle_br is None or active_br is None:
        # Do not fake compliance from main/sub naming. Pixel count is informational only.
        idle_px = idle.get("pixels")
        active_px = active.get("pixels")
        result["compliance"] = UNKNOWN
        result["pixel_hint"] = None
        if idle_px and active_px:
            result["pixel_hint"] = "idle_lower" if idle_px < active_px else (
                "idle_not_lower" if idle_px >= active_px else None
            )
        result["message"] = (
            "Configured bitrate unknown for one or both streams; "
            "compliance not claimed (main/sub label alone is insufficient)"
        )
        return result

    if idle_br < active_br:
        result["compliance"] = COMPLIANT
        result["message"] = (
            f"Idle bitrate {idle_br} kbps < active bitrate {active_br} kbps"
        )
    else:
        result["compliance"] = NON_COMPLIANT
        result["message"] = (
            f"Idle bitrate {idle_br} kbps is not lower than active {active_br} kbps"
        )
    return result


async def evaluate_motion_bitrate_compliance(
    camera_id: str,
    *,
    idle_stream: str = "sub",
    active_stream: str = "main",
) -> dict[str, Any]:
    """Read live stream profiles and compare idle vs active bitrates (read-only)."""
    from app.services.stream_profile_service import get_camera_stream_profile

    profile = await get_camera_stream_profile(camera_id)
    if not profile.get("ok"):
        return {
            "compliance": UNKNOWN,
            "bitrate_known": False,
            "message": profile.get("error") or "Could not read stream profiles",
            "idle": None,
            "active": None,
            "driver": profile.get("driver"),
        }

    idle_key = "sub" if idle_stream == "sub" else "main"
    active_key = "main" if active_stream == "main" else "sub"
    idle_block = profile.get(idle_key)
    active_block = profile.get(active_key)
    compared = compare_idle_active_profiles(idle_block, active_block)
    compared["camera_id"] = camera_id
    compared["driver"] = profile.get("driver")
    compared["idle_stream"] = idle_stream
    compared["active_stream"] = active_stream
    compared["reconfigures_on_motion"] = False
    return compared


def estimate_segment_bitrate_kbps(session_dir, *, sample: int = 3) -> Optional[float]:
    """Estimate archive bitrate from recent .ts segment sizes / durations (best-effort)."""
    from pathlib import Path

    root = Path(session_dir)
    if not root.is_dir():
        return None
    segs = sorted(root.glob("seg_*.ts"))
    if not segs:
        segs = sorted(root.glob("*.ts"))
    if not segs:
        return None
    segs = segs[-max(1, sample) :]
    total_bytes = 0
    for p in segs:
        try:
            total_bytes += p.stat().st_size
        except OSError:
            continue
    # Assume ~2–4s segments if playlist target unknown; use 4s default for estimate
    duration = 4.0 * len(segs)
    if duration <= 0 or total_bytes <= 0:
        return None
    return round((total_bytes * 8 / duration) / 1000.0, 1)
