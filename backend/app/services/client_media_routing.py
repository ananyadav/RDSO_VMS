"""RDSO 18.1.19 / 18.1.20 — client media routing without recording-server URLs.

Clients resolve live and playback through centralized camera identity and
relative VMS paths. Recording-server ownership is metadata only — never a
client-facing host/credential.
"""

from __future__ import annotations

from typing import Any, Optional


def live_ws_path(worker_id: int | None) -> str:
    wid = int(worker_id or 1)
    if wid <= 0:
        wid = 1
    return f"/media/w{wid}/api/ws"


def frame_jpeg_path(worker_id: int | None, src: str) -> str:
    """On-demand still via Nginx → go2rtc (same auth as live WS). Never direct camera."""
    from urllib.parse import quote

    wid = int(worker_id or 1)
    if wid <= 0:
        wid = 1
    return f"/media/w{wid}/api/frame.jpeg?src={quote(str(src or ''), safe='')}"


def playback_media_path_template() -> str:
    """Relative playlist path — camera + session only (no recording server host)."""
    return "/api/playback/{camera_id}/{session_id}/media/index.m3u8"


def build_client_media_routing(camera: dict[str, Any]) -> dict[str, Any]:
    """Public routing descriptor for one camera (no secrets, no server base URLs)."""
    cam_id = str(camera.get("_id") or camera.get("id") or "").strip()
    uid = str(camera.get("camera_uid") or camera.get("cameraUid") or "").strip()
    worker = camera.get("worker_id", camera.get("workerId"))
    try:
        worker_id = int(worker) if worker not in (None, "") else 1
    except (TypeError, ValueError):
        worker_id = 1
    if worker_id <= 0:
        worker_id = 1

    # Ownership metadata may change on failover; client identity must not.
    home_server = camera.get("recording_server_id") or None
    stream_base = uid or cam_id
    snap_src = f"{stream_base}_sub" if stream_base else ""

    return {
        "camera_id": cam_id,
        "camera_uid": uid,
        "identity_stable": True,
        "seamless": True,
        "live": {
            "worker_id": worker_id,
            "ws_path": live_ws_path(worker_id),
            "stream_src_hint": stream_base,
            "dynamic_switch": True,
            "notes": "Select/change cameras in Live View without restart; routes via Nginx → go2rtc worker.",
            # Browser always receives VMS-relayed unicast (even if camera ingest is multicast).
            "browser_transport": "unicast",
            "relative_path": True,
        },
        "snapshot": {
            "on_demand": True,
            "via_vms_go2rtc": True,
            "frame_jpeg_path": frame_jpeg_path(worker_id, snap_src) if snap_src else None,
            "src": snap_src or None,
            "worker_id": worker_id,
            "direct_camera": False,
            "notes": "CCC/VMS on-demand JPEG via /media/wN/api/frame.jpeg — never camera RTSP/ONVIF.",
        },
        "playback": {
            "search_by": "camera_uid",
            "media_path_template": playback_media_path_template(),
            "independent_of_recording_server": True,
            "notes": "Playback search/play use camera identity across any recording_server_id.",
        },
        # Optional ops hint — never a connect URL or credential.
        "recording_home_server_id": str(home_server) if home_server else None,
        "client_must_not_use_server_host": True,
        "rdso_18_2_2": True,
        "network": {
            "lan_wan_wlan": True,
            "relative_urls_only": True,
            "no_camera_credentials": True,
        },
        "remote_transcode": {
            "rdso_18_5_ii": True,
            "capability_path": "/api/remote-transcode/capability",
            "start_path": "/api/remote-transcode/sessions",
            "on_demand_only": True,
            "live_source": "go2rtc_local_rtsp",
            "lan_direct_unchanged": True,
        },
        "transport": _client_transport_block(camera),
    }


def _client_transport_block(camera: dict[str, Any]) -> dict[str, Any]:
    try:
        from app.services.network_video_transport import transport_capability_public

        cap = transport_capability_public(camera=camera)
        # Strip anything that could look like a connect URL for browsers.
        mcam = (cap.get("multicast") or {}).get("camera") or {}
        mcam = {
            "enabled": bool(mcam.get("enabled")),
            "browser_delivery": mcam.get("browser_delivery") or "unicast_relay",
            "browser_native_multicast": False,
            "message": mcam.get("message") or "",
            "config": {
                "enabled": bool((mcam.get("config") or {}).get("enabled")),
                "source_mode": (mcam.get("config") or {}).get("source_mode") or "disabled",
                # Address/port are ops topology — omit from browser client-media.
            },
        }
        return {
            "unicast_default": True,
            "browser_delivery": "unicast",
            "multicast_ingest": mcam,
            "rdso_18_2_3": True,
            "rdso_18_2_27": True,
        }
    except Exception:
        return {
            "unicast_default": True,
            "browser_delivery": "unicast",
            "rdso_18_2_3": True,
            "rdso_18_2_27": True,
        }


def sessions_findable_across_servers(
    sessions: list[dict[str, Any]],
    *,
    camera_id: str,
) -> list[dict[str, Any]]:
    """Filter sessions by camera only — ignores recording_server_id (18.1.20)."""
    cid = str(camera_id or "").strip()
    return [s for s in sessions if str(s.get("camera_id") or "") == cid]


async def list_recording_servers_public(
    *,
    healthy_only: bool = False,
    enabled_only: bool = False,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Authenticated management list — unbounded registry (18.1.17)."""
    from app.services.recording_ha_coordinator import refresh_server_health_flags
    from app.services.recording_ha_store import get_ha_store
    from app.services.recording_ha_types import public_server
    from app.services.recording_server_config import (
        local_recording_server_id,
        recording_ha_enabled,
        recording_server_count_limit,
    )

    await refresh_server_health_flags()
    store = get_ha_store()
    raw = await store.list_servers()
    items = []
    for doc in raw:
        pub = public_server(doc)
        if not pub:
            continue
        if healthy_only and not pub.get("healthy"):
            continue
        if enabled_only and not pub.get("enabled"):
            continue
        items.append(pub)

    # Sort for stable UI; never truncate unless caller passes an explicit page limit.
    items.sort(key=lambda s: str(s.get("server_id") or ""))
    total = len(items)
    if limit is not None:
        limit_n = max(1, int(limit))
        items = items[:limit_n]
    else:
        limit_n = None

    return {
        "items": items,
        "total": total,
        "returned": len(items),
        "limit": limit_n,
        "server_count_limit": recording_server_count_limit(),
        "ha_enabled": recording_ha_enabled(),
        "local_server_id": local_recording_server_id(),
        "rdso_18_1_17": True,
    }
