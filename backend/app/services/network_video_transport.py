"""RDSO 18.2.2 / 18.2.3 / 18.2.27 — network video transport (LAN/WAN + uni/multicast).

Design:
- Browser clients always use relative same-origin API/media paths (LAN/WAN/WLAN safe).
- Default camera ingest remains unicast RTSP/TCP via go2rtc.
- Optional per-camera multicast *source* config for VMS ingest (UDP MPEG-TS / RTP / RTSP multicast).
- Browser delivery stays VMS-relayed unicast (/media/wN) — browsers cannot join IP multicast.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Optional

SOURCE_MODE_DISABLED = "disabled"
SOURCE_MODE_UDP_MPEGTS = "udp_mpegts"
SOURCE_MODE_RTP = "rtp"
SOURCE_MODE_RTSP_MULTICAST = "rtsp_multicast"

SOURCE_MODES = frozenset(
    {
        SOURCE_MODE_DISABLED,
        SOURCE_MODE_UDP_MPEGTS,
        SOURCE_MODE_RTP,
        SOURCE_MODE_RTSP_MULTICAST,
    }
)

# IPv4 multicast range (RFC 5771 / Class D)
_MULTICAST_NET = ipaddress.ip_network("224.0.0.0/4")


class MulticastConfigError(ValueError):
    pass


def is_ipv4_multicast_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr or "").strip())
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip in _MULTICAST_NET


def validate_multicast_address(addr: str) -> str:
    text = str(addr or "").strip()
    if not text:
        raise MulticastConfigError("multicast address is required when enabled")
    if not is_ipv4_multicast_address(text):
        raise MulticastConfigError(
            f"invalid multicast address '{text}' — must be IPv4 in 224.0.0.0–239.255.255.255"
        )
    return text


def validate_port(port: Any) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise MulticastConfigError("multicast port must be an integer 1–65535") from exc
    if value < 1 or value > 65535:
        raise MulticastConfigError("multicast port must be an integer 1–65535")
    return value


def normalize_source_mode(raw: Any) -> str:
    mode = str(raw or SOURCE_MODE_DISABLED).strip().lower().replace("-", "_")
    aliases = {
        "udp": SOURCE_MODE_UDP_MPEGTS,
        "mpegts": SOURCE_MODE_UDP_MPEGTS,
        "udp_mpeg_ts": SOURCE_MODE_UDP_MPEGTS,
        "rtsp": SOURCE_MODE_RTSP_MULTICAST,
        "off": SOURCE_MODE_DISABLED,
        "none": SOURCE_MODE_DISABLED,
        "unicast": SOURCE_MODE_DISABLED,
    }
    mode = aliases.get(mode, mode)
    if mode not in SOURCE_MODES:
        raise MulticastConfigError(
            f"unsupported source_mode '{raw}' — use disabled|udp_mpegts|rtp|rtsp_multicast"
        )
    return mode


def default_multicast_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "address": "",
        "port": 5004,
        "source_mode": SOURCE_MODE_DISABLED,
        "ttl": 1,
        "path": "",  # optional RTSP path for rtsp_multicast
    }


def public_multicast_config(raw: Any) -> dict[str, Any]:
    """Sanitize stored config for API responses (no secrets)."""
    base = default_multicast_config()
    if not isinstance(raw, dict):
        return base
    enabled = bool(raw.get("enabled"))
    mode = str(raw.get("source_mode") or SOURCE_MODE_DISABLED).strip().lower()
    if mode not in SOURCE_MODES:
        mode = SOURCE_MODE_DISABLED
    try:
        port = int(raw.get("port") or 5004)
    except (TypeError, ValueError):
        port = 5004
    try:
        ttl = int(raw.get("ttl") or 1)
    except (TypeError, ValueError):
        ttl = 1
    addr = str(raw.get("address") or "").strip()
    return {
        "enabled": enabled and mode != SOURCE_MODE_DISABLED and bool(addr),
        "address": addr,
        "port": port,
        "source_mode": mode if enabled else SOURCE_MODE_DISABLED,
        "ttl": max(1, min(ttl, 255)),
        "path": str(raw.get("path") or "").strip(),
    }


def normalize_multicast_config(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    """Validate and normalize multicast config for persistence."""
    if raw is None:
        if partial:
            return {}
        return default_multicast_config()
    if not isinstance(raw, dict):
        raise MulticastConfigError("multicast must be an object")

    enabled = bool(raw.get("enabled"))
    mode = normalize_source_mode(raw.get("source_mode") or (SOURCE_MODE_UDP_MPEGTS if enabled else SOURCE_MODE_DISABLED))
    if not enabled:
        mode = SOURCE_MODE_DISABLED

    cfg = default_multicast_config()
    cfg["enabled"] = enabled and mode != SOURCE_MODE_DISABLED
    cfg["source_mode"] = mode if cfg["enabled"] else SOURCE_MODE_DISABLED
    cfg["path"] = str(raw.get("path") or "").strip()

    if "ttl" in raw and raw.get("ttl") not in (None, ""):
        try:
            cfg["ttl"] = max(1, min(int(raw.get("ttl")), 255))
        except (TypeError, ValueError) as exc:
            raise MulticastConfigError("ttl must be an integer 1–255") from exc

    if cfg["enabled"]:
        cfg["address"] = validate_multicast_address(raw.get("address"))
        cfg["port"] = validate_port(raw.get("port") if raw.get("port") not in (None, "") else 5004)
    else:
        # Allow storing draft address/port while disabled (still validate if present)
        addr = str(raw.get("address") or "").strip()
        if addr:
            cfg["address"] = validate_multicast_address(addr)
        if raw.get("port") not in (None, ""):
            cfg["port"] = validate_port(raw.get("port"))
    return cfg


def build_multicast_ingest_url(cfg: dict[str, Any]) -> Optional[str]:
    """Build a go2rtc-compatible ingest URL for an enabled multicast source.

    Returns None when disabled/invalid. Does not embed camera credentials.
    """
    pub = public_multicast_config(cfg)
    if not pub.get("enabled"):
        return None
    addr = pub["address"]
    port = int(pub["port"])
    mode = pub["source_mode"]
    if mode == SOURCE_MODE_UDP_MPEGTS:
        # go2rtc ffmpeg UDP MPEG-TS ingest → H.264 for browser relay
        return f"ffmpeg:udp://@{addr}:{port}?localaddr=0.0.0.0#video=h264#audio=copy"
    if mode == SOURCE_MODE_RTP:
        return f"ffmpeg:rtp://{addr}:{port}#video=h264#audio=copy"
    if mode == SOURCE_MODE_RTSP_MULTICAST:
        path = pub.get("path") or "/"
        if not path.startswith("/"):
            path = "/" + path
        # Multicast RTSP URL (no credentials — join is network-level)
        return f"rtsp://{addr}:{port}{path}"
    return None


def is_multicast_ingest_url(url: str) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return False
    if text.startswith("ffmpeg:udp://") or text.startswith("ffmpeg:rtp://"):
        return True
    if text.startswith("udp://") or text.startswith("rtp://"):
        return True
    # rtsp://239.x.x.x/...
    m = re.match(r"^rtsp://([^/:]+)", text)
    if m and is_ipv4_multicast_address(m.group(1)):
        return True
    return False


def camera_multicast_status(camera: dict[str, Any]) -> dict[str, Any]:
    """Honest per-camera multicast ingest status for APIs."""
    cfg = public_multicast_config(camera.get("multicast") or camera.get("network_transport", {}).get("multicast"))
    ingest = None
    error = None
    try:
        if cfg.get("enabled"):
            # Re-validate
            normalize_multicast_config(cfg)
            ingest = build_multicast_ingest_url(cfg)
    except MulticastConfigError as exc:
        error = str(exc)
        cfg["enabled"] = False

    return {
        "enabled": bool(cfg.get("enabled") and ingest and not error),
        "config": cfg,
        "ingest_url_present": bool(ingest),
        # Never return the full ingest URL to browser clients (may leak topology); ops APIs can include masked.
        "error": error,
        "browser_delivery": "unicast_relay",
        "browser_native_multicast": False,
        "message": (
            "Multicast source enabled for VMS ingest; browsers receive VMS-relayed unicast via /media/wN"
            if cfg.get("enabled") and ingest and not error
            else (
                error
                or (
                    "Multicast disabled — default unicast RTSP ingest"
                    if not cfg.get("enabled")
                    else "Multicast configured but ingest URL unavailable"
                )
            )
        ),
    }


def network_connectivity_public() -> dict[str, Any]:
    """RDSO 18.2.2 — client connectivity model (LAN/WAN/WLAN)."""
    return {
        "rdso_18_2_2": True,
        "client_urls": "relative_same_origin",
        "api_paths": "/api/*",
        "live_media_paths": "/media/w{N}/api/ws",
        "camera_credentials_to_browser": False,
        "lan_only_assumption": False,
        "works_when": (
            "Browser can reach the VMS Nginx/API host over LAN, WAN, or WLAN "
            "(routed IP connectivity). Absolute recording-server hosts are not used."
        ),
        "webrtc_note": (
            "WebRTC ICE may need GO2RTC_WEBRTC_HOST reachable from the client network; "
            "MSE over /media/wN tracks the browser HTTP host and remains the WAN-safe default path."
        ),
    }


def transport_capability_public(*, camera: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """RDSO 18.2.3 / 18.2.27 capability snapshot."""
    multicast = camera_multicast_status(camera) if camera else {
        "enabled": False,
        "browser_delivery": "unicast_relay",
        "browser_native_multicast": False,
        "message": "Per-camera multicast source config available via /api/cameras/{id}/transport",
    }
    return {
        "rdso_18_2_3": True,
        "rdso_18_2_27": True,
        "unicast": {
            "supported": True,
            "default": True,
            "camera_ingest": "rtsp_tcp",
            "browser_delivery": ["mse", "webrtc", "mjpeg"],
            "path": "/media/w{N}/api/ws",
        },
        "multicast": {
            "source_ingest_supported": True,
            "browser_native_multicast": False,
            "browser_delivery": "vms_relayed_unicast",
            "source_modes": sorted(SOURCE_MODES - {SOURCE_MODE_DISABLED}),
            "address_range": "224.0.0.0–239.255.255.255",
            "camera": multicast,
            "network_acceptance_required": (
                "IGMP/multicast-enabled LAN between cameras and VMS host; "
                "not claimed by unit tests alone."
            ),
        },
        "network": network_connectivity_public(),
    }


def system_transport_status() -> dict[str, Any]:
    cap = transport_capability_public()
    return {
        **cap,
        "rdso_18_2_2": True,
        "security": {
            "rtsp_credentials_masked": True,
            "client_media_excludes_secrets": True,
            "rbac_acl_enforced": True,
        },
    }
