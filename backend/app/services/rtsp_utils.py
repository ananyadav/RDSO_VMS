"""Shared RTSP URL helpers for cameras."""

import os
import urllib.parse
from typing import Dict


def _preview_channel(camera_doc: dict) -> str:
    return str(
        camera_doc.get("preview_channel")
        or os.getenv("CAMERA_PREVIEW_CHANNEL", "103")
    ).strip()


def build_rtsp_path(
    model: str,
    channel: str = "102",
    *,
    main: bool = False,
    preview: bool = False,
) -> str:
    """
    Hikvision-style paths:
      101 — main (recording, H.265+)
      102 — sub  (grid, H.264 low res)
      103 — preview (fullscreen live, H.264 ~1080p)
    """
    model_l = (model or "").lower()
    ch = str(channel or "102").strip()

    if "hikvision" in model_l or "hik" in model_l:
        if preview:
            use_ch = os.getenv("CAMERA_PREVIEW_CHANNEL", "103").strip() or ch or "103"
            if ch and ch != "102":
                use_ch = ch
            return f"/Streaming/Channels/{use_ch}"
        use_ch = ch or ("101" if main else "102")
        return f"/Streaming/Channels/{use_ch}"

    if "dahua" in model_l:
        if preview:
            return "/cam/realmonitor?channel=1&subtype=0"
        subtype = "0" if main else "1"
        return f"/cam/realmonitor?channel=1&subtype={subtype}"

    if "axis" in model_l:
        return "/axis-media/media.amp"

    if preview:
        return f"/Streaming/Channels/103"
    use_ch = "101" if main else ch
    return f"/Streaming/Channels/{use_ch}"


def build_rtsp_url(
    *,
    ip_address: str,
    port: int,
    username: str,
    password: str,
    model: str = "",
    channel: str = "102",
    main: bool = False,
    preview: bool = False,
    preview_channel: str = "103",
) -> str:
    username_encoded = urllib.parse.quote((username or "admin").strip(), safe="")
    password_encoded = urllib.parse.quote(str(password).strip(), safe="")
    path = build_rtsp_path(
        model,
        preview_channel if preview else channel,
        main=main,
        preview=preview,
    )
    return f"rtsp://{username_encoded}:{password_encoded}@{ip_address}:{port}{path}"


def rewrite_rtsp_credentials(rtsp_url: str, username: str, password: str) -> str:
    """Replace user:pass in an RTSP URL, keeping host/path unchanged."""
    if not rtsp_url or "://" not in rtsp_url:
        return rtsp_url
    scheme, rest = rtsp_url.split("://", 1)
    if "@" not in rest:
        return rtsp_url
    _, host_path = rest.split("@", 1)
    user = urllib.parse.quote((username or "admin").strip(), safe="")
    pwd = urllib.parse.quote(str(password).strip(), safe="")
    return f"{scheme}://{user}:{pwd}@{host_path}"


def rtsp_url_credentials_stale(camera_doc: dict) -> bool:
    """True when stored RTSP URLs embed a different user/pass than the camera doc."""
    password = str(camera_doc.get("password") or "")
    username = (camera_doc.get("username") or "admin").strip()
    for key in ("main_rtsp_url", "sub_rtsp_url", "preview_rtsp_url", "rtsp_url"):
        url = (camera_doc.get(key) or "").strip()
        if not url or "://" not in url or "@" not in url:
            continue
        auth = url.split("://", 1)[1].split("@", 1)[0]
        if ":" not in auth:
            continue
        user_part, pass_part = auth.split(":", 1)
        if urllib.parse.unquote(user_part) != username:
            return True
        if urllib.parse.unquote(pass_part) != password:
            return True
    return False


def sync_camera_rtsp_urls(camera_doc: dict) -> dict:
    """Rebuild auto Hikvision URLs or refresh credentials in manual URLs."""
    doc = dict(camera_doc)
    protocol = (doc.get("protocol") or "HIKVISION").upper()
    if protocol in ("ONVIF", "CUSTOM"):
        username = (doc.get("username") or "admin").strip()
        password = doc.get("password") or ""
        for key in ("main_rtsp_url", "sub_rtsp_url", "preview_rtsp_url"):
            if doc.get(key):
                doc[key] = rewrite_rtsp_credentials(doc[key], username, password)
        sub = (doc.get("sub_rtsp_url") or doc.get("rtsp_url") or "").strip()
        if sub:
            doc["rtsp_url"] = sub
        doc["rtsp_url_source"] = doc.get("rtsp_url_source") or (
            "onvif" if protocol == "ONVIF" else "custom"
        )
        return doc
    return apply_rtsp_urls(doc, force_auto=True)


def apply_rtsp_urls(camera_doc: dict, *, force_auto: bool = False) -> dict:
    """Set RTSP URL fields from protocol/channels. Auto for HIKVISION unless manual."""
    doc = dict(camera_doc)
    protocol = (doc.get("protocol") or "HIKVISION").upper()
    if protocol in ("ONVIF", "CUSTOM") and not force_auto:
        sub = (doc.get("sub_rtsp_url") or doc.get("rtsp_url") or "").strip()
        if sub:
            doc["rtsp_url"] = sub
        doc["rtsp_url_source"] = doc.get("rtsp_url_source") or (
            "onvif" if protocol == "ONVIF" else "custom"
        )
        return doc

    model = doc.get("model") or ""
    if protocol == "HIKVISION" and not model:
        model = "Hikvision"

    main_ch = str(doc.get("main_channel") or "101").strip()
    sub_ch = str(doc.get("sub_channel") or doc.get("recording_channel") or "102").strip()
    preview_ch = str(doc.get("preview_channel") or "103").strip()

    doc["main_channel"] = main_ch
    doc["sub_channel"] = sub_ch
    doc["recording_channel"] = sub_ch
    doc["preview_channel"] = preview_ch
    doc["model"] = model

    urls = build_camera_rtsp_urls(doc)
    doc.update(urls)
    doc["rtsp_url_source"] = "auto_hikvision"
    return doc


def effective_camera_rtsp_urls(camera_doc: dict) -> Dict[str, str]:
    """Current RTSP URLs — always derived from stored credentials, not stale URL fields."""
    protocol = (camera_doc.get("protocol") or "HIKVISION").upper()
    if protocol in ("ONVIF", "CUSTOM"):
        synced = sync_camera_rtsp_urls(camera_doc)
        sub = (synced.get("sub_rtsp_url") or synced.get("rtsp_url") or "").strip()
        main = (synced.get("main_rtsp_url") or sub).strip()
        if sub:
            preview = (synced.get("preview_rtsp_url") or sub).strip()
            return {
                "main_rtsp_url": main,
                "sub_rtsp_url": sub,
                "preview_rtsp_url": preview,
                "rtsp_url": sub,
            }
    return build_camera_rtsp_urls(camera_doc)


def build_camera_rtsp_urls(camera_doc: dict) -> Dict[str, str]:
    """main=101 record, sub=102 grid, preview=103 fullscreen live (H.264)."""
    ip_address = (camera_doc.get("ip_address") or "").strip()
    port = int(camera_doc.get("port") or 554)
    username = (camera_doc.get("username") or "admin").strip()
    password = camera_doc.get("password") or ""
    model = camera_doc.get("model") or ""
    sub_ch = str(camera_doc.get("sub_channel") or camera_doc.get("recording_channel") or "102").strip()
    main_ch = str(camera_doc.get("main_channel") or "101").strip()
    preview_ch = _preview_channel(camera_doc)

    main = build_rtsp_url(
        ip_address=ip_address,
        port=port,
        username=username,
        password=password,
        model=model,
        channel=main_ch,
        main=True,
    )
    sub = build_rtsp_url(
        ip_address=ip_address,
        port=port,
        username=username,
        password=password,
        model=model,
        channel=sub_ch,
        main=False,
    )
    preview = build_rtsp_url(
        ip_address=ip_address,
        port=port,
        username=username,
        password=password,
        model=model,
        channel=sub_ch,
        preview=True,
        preview_channel=preview_ch,
    )
    return {
        "main_rtsp_url": main,
        "sub_rtsp_url": sub,
        "preview_rtsp_url": preview,
        "preview_channel": preview_ch,
        "rtsp_url": sub,
        "recording_channel": sub_ch,
        "main_channel": main_ch,
        "sub_channel": sub_ch,
    }


def mask_rtsp_url(rtsp_url: str) -> str:
    if "@" not in rtsp_url:
        return rtsp_url
    return rtsp_url.split("://")[0] + "://" + rtsp_url.split("@", 1)[-1]
