"""RDSO 18.5(ii) — Auto bandwidth profiles for remote live/playback transcoding.

Selects FPS, resolution, and H.264 bitrate from a configured client bandwidth
limit. LAN/direct clients keep the existing go2rtc stream-copy path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class BandwidthProfile:
    id: str
    label: str
    max_bandwidth_kbps: int  # upper bound for Auto selection (inclusive)
    width: int
    height: int
    fps: int
    video_bitrate_kbps: int
    audio_bitrate_kbps: int
    codec: str = "libx264"
    pixel_format: str = "yuv420p"

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["codec_family"] = "h264"
        return d


# Ordered low → high for Auto selection.
BANDWIDTH_PROFILES: tuple[BandwidthProfile, ...] = (
    BandwidthProfile(
        id="very_low",
        label="Very low (≈128–256 kbps)",
        max_bandwidth_kbps=256,
        width=320,
        height=180,
        fps=5,
        video_bitrate_kbps=120,
        audio_bitrate_kbps=24,
    ),
    BandwidthProfile(
        id="low",
        label="Low (≈256–512 kbps)",
        max_bandwidth_kbps=512,
        width=640,
        height=360,
        fps=10,
        video_bitrate_kbps=280,
        audio_bitrate_kbps=32,
    ),
    BandwidthProfile(
        id="medium",
        label="Medium (≈0.5–1.5 Mbps)",
        max_bandwidth_kbps=1500,
        width=854,
        height=480,
        fps=15,
        video_bitrate_kbps=700,
        audio_bitrate_kbps=48,
    ),
    BandwidthProfile(
        id="high",
        label="High (≈1.5–4 Mbps)",
        max_bandwidth_kbps=4000,
        width=1280,
        height=720,
        fps=20,
        video_bitrate_kbps=1800,
        audio_bitrate_kbps=64,
    ),
    BandwidthProfile(
        id="ultra",
        label="Ultra (≥4 Mbps)",
        max_bandwidth_kbps=10_000_000,
        width=1920,
        height=1080,
        fps=25,
        video_bitrate_kbps=3500,
        audio_bitrate_kbps=96,
    ),
)

_PROFILE_BY_ID = {p.id: p for p in BANDWIDTH_PROFILES}

# At/above this Auto prefers the unchanged LAN go2rtc/copy path when mode allows.
DIRECT_PATH_BANDWIDTH_KBPS = 8000


def get_profile(profile_id: str) -> Optional[BandwidthProfile]:
    return _PROFILE_BY_ID.get((profile_id or "").strip().lower())


def list_profiles_public() -> list[dict[str, Any]]:
    return [p.public() for p in BANDWIDTH_PROFILES]


def select_profile_for_bandwidth(bandwidth_kbps: int | float | None) -> BandwidthProfile:
    """Auto mode: pick the highest profile whose encode fits the client budget."""
    if bandwidth_kbps is None:
        return _PROFILE_BY_ID["medium"]
    try:
        kbps = float(bandwidth_kbps)
    except (TypeError, ValueError):
        return _PROFILE_BY_ID["medium"]
    if kbps <= 0:
        return BANDWIDTH_PROFILES[0]
    chosen = BANDWIDTH_PROFILES[0]
    for profile in BANDWIDTH_PROFILES:
        needed = (profile.video_bitrate_kbps + profile.audio_bitrate_kbps) * 1.2
        if needed <= kbps:
            chosen = profile
        else:
            break
    return chosen


def resolve_transcode_intent(
    *,
    mode: str = "auto",
    bandwidth_kbps: int | float | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """
    Decide whether to transcode and which profile to use.

    - mode=direct → no transcode (LAN/local stream-copy unchanged)
    - mode=profile → named profile (must exist)
    - mode=auto → select from bandwidth; may recommend direct at high bandwidth
    """
    m = (mode or "auto").strip().lower()
    if m in ("direct", "copy", "lan", "local"):
        return {
            "transcode": False,
            "mode": "direct",
            "profile": None,
            "reason": "LAN/local direct stream-copy path (go2rtc / archived HLS as stored)",
            "recommend_direct": True,
        }

    if m == "profile":
        profile = get_profile(profile_id or "")
        if not profile:
            return {
                "transcode": False,
                "mode": "profile",
                "profile": None,
                "error": f"Unknown profile: {profile_id!r}",
                "recommend_direct": False,
            }
        return {
            "transcode": True,
            "mode": "profile",
            "profile": profile,
            "reason": f"explicit profile {profile.id}",
            "recommend_direct": False,
        }

    # auto
    try:
        kbps_f = float(bandwidth_kbps) if bandwidth_kbps is not None else None
    except (TypeError, ValueError):
        kbps_f = None

    if kbps_f is not None and kbps_f >= DIRECT_PATH_BANDWIDTH_KBPS:
        return {
            "transcode": False,
            "mode": "auto",
            "profile": None,
            "bandwidth_kbps": kbps_f,
            "reason": (
                f"Auto: bandwidth {kbps_f:.0f} kbps ≥ {DIRECT_PATH_BANDWIDTH_KBPS} "
                "— use direct LAN/go2rtc path (no fleet transcode)"
            ),
            "recommend_direct": True,
        }

    profile = select_profile_for_bandwidth(kbps_f)
    return {
        "transcode": True,
        "mode": "auto",
        "profile": profile,
        "bandwidth_kbps": kbps_f,
        "reason": f"Auto selected profile {profile.id} for ~{kbps_f or 'default'} kbps",
        "recommend_direct": False,
    }


def remote_transcode_capability_public() -> dict[str, Any]:
    return {
        "rdso_18_5_ii": True,
        "on_demand_only": True,
        "fleet_wide_transcoding": False,
        "lan_direct_unchanged": True,
        "live_source": "go2rtc_local_rtsp",
        "playback_source": "recording_session_hls",
        "never_direct_camera_rtsp": True,
        "never_exposes_camera_credentials": True,
        "auto_bandwidth_mode": True,
        "profiles": list_profiles_public(),
        "direct_path_bandwidth_kbps": DIRECT_PATH_BANDWIDTH_KBPS,
        "output": {
            "container": "hls",
            "video_codec": "h264",
            "adapts": ["fps", "resolution", "bitrate", "compression"],
        },
    }
