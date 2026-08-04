"""Shared RTSP URL helpers for multi-brand cameras."""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional

AUTO_RTSP_BRANDS = frozenset(
    {
        "HIKVISION",
        "PRAMA",
        "DAHUA",
        "UNIVIEW",
        "VIVOTEK",
        "HONEYWELL",
        "SPARSH",
    }
)

MAKE_ALIASES = {
    "HIK": "HIKVISION",
    "UNV": "UNIVIEW",
}


def normalize_make(make: str) -> str:
    """Normalize brand / protocol name for RTSP template selection."""
    brand = (make or "HIKVISION").strip().upper()
    return MAKE_ALIASES.get(brand, brand)


def _encode_credentials(username: str, password: str) -> tuple[str, str]:
    user = urllib.parse.quote((username or "admin").strip(), safe="")
    pwd = urllib.parse.quote(str(password or "").strip(), safe="")
    return user, pwd


def _assemble_url(
    *,
    scheme: str,
    ip: str,
    port: int,
    path: str,
    username: str,
    password: str,
    include_port: bool = True,
) -> str:
    user, pwd = _encode_credentials(username, password)
    host = (ip or "").strip()
    if not host:
        return ""
    if scheme == "onvif":
        return f"onvif://{user}:{pwd}@{host}"
    port = int(port or 554)
    suffix = f":{port}" if include_port else ""
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"rtsp://{user}:{pwd}@{host}{suffix}{normalized_path}"


def _hikvision_paths() -> Dict[str, Any]:
    return {
        "main_path": "/Streaming/Channels/101",
        "sub_path": "/Streaming/Channels/102",
        "main_channel": "101",
        "sub_channel": "102",
        "rtsp_source": "auto_hikvision",
    }


def _dahua_paths() -> Dict[str, Any]:
    return {
        "main_path": "/cam/realmonitor?channel=1&subtype=0",
        "sub_path": "/cam/realmonitor?channel=1&subtype=1",
        "main_channel": "1",
        "sub_channel": "1",
        "rtsp_source": "auto_dahua",
    }


def _uniview_paths() -> Dict[str, Any]:
    # video1/video2 are typically full/sub quality (often HEVC).
    # Browser live wraps these with ffmpeg→H.264 in stream_source_urls.
    return {
        "main_path": "/media/video1",
        "sub_path": "/media/video2",
        "main_channel": "1",
        "sub_channel": "2",
        "rtsp_source": "auto_uniview",
        "fallback_paths": ["/media/video3"],
        "browser_transcode_h264": True,
    }


def _brand_path_spec(make: str) -> Dict[str, Any]:
    brand = normalize_make(make)
    if brand in ("HIKVISION", "PRAMA"):
        spec = _hikvision_paths()
        if brand == "PRAMA":
            # Prama often ships H.265 on Hik-style paths; browsers need H.264.
            spec = {
                **spec,
                "rtsp_source": "auto_prama",
                "browser_transcode_h264": True,
            }
        return spec
    if brand == "DAHUA":
        return _dahua_paths()
    if brand == "UNIVIEW":
        return _uniview_paths()
    if brand == "VIVOTEK":
        return {
            "main_path": "/live1s1.sdp",
            "sub_path": "/live1s2.sdp",
            "main_channel": "1",
            "sub_channel": "2",
            "rtsp_source": "auto_vivotek",
            "fallback_paths": ["/live.sdp"],
        }
    if brand == "HONEYWELL":
        return {
            "main_path": "/rtsp/streaming?channel=01&subtype=A",
            "sub_path": "/rtsp/streaming?channel=01&subtype=B",
            "main_channel": "01",
            "sub_channel": "01",
            "rtsp_source": "auto_honeywell",
            "fallback_paths": [
                "/rtsp/streaming?channel=01&subtype=0",
                "/rtsp/streaming?channel=01&subtype=1",
                "/h264",
                "/cam1/h264",
                "/PSIA/Streaming/channels/1",
            ],
        }
    if brand == "SPARSH":
        return {
            # Sparsh's RTSP server accepts these paths. Hikvision-style
            # /Streaming/Channels/* DESCRIBEs but fails during SETUP.
            "main_path": "/ch01.264?ptype=tcp&dev=1",
            "sub_path": "/ch01_sub.264?ptype=tcp&dev=1",
            "main_channel": "ch01",
            "sub_channel": "ch01_sub",
            "rtsp_source": "auto_sparsh",
        }
    return _hikvision_paths()


def build_rtsp_urls(
    *,
    make: str,
    ip: str,
    username: str,
    password: str,
    port: int = 554,
) -> Dict[str, Any]:
    """
    Build brand-specific RTSP URLs and channel metadata.

    Returns protocol, main/sub/recording channels, main/sub RTSP URLs,
    rtsp_source, and optional fallback_urls (for go2rtc failover).
    """
    brand = normalize_make(make)
    protocol = brand if brand in AUTO_RTSP_BRANDS else brand
    spec = _brand_path_spec(brand)
    port = int(port or 554)

    if brand == "SPARSH":
        main_url = _assemble_url(
            scheme="rtsp",
            ip=ip,
            port=port,
            path=spec["main_path"],
            username=username,
            password=password,
        )
        sub_url = _assemble_url(
            scheme="rtsp",
            ip=ip,
            port=port,
            path=spec["sub_path"],
            username=username,
            password=password,
        )
        fallbacks: List[str] = [
            _assemble_url(
                scheme="onvif",
                ip=ip,
                port=port,
                path="",
                username=username,
                password=password,
                include_port=False,
            )
        ]
        for template_make in ("DAHUA", "UNIVIEW"):
            tpl = build_rtsp_urls(
                make=template_make,
                ip=ip,
                username=username,
                password=password,
                port=port,
            )
            for key in ("main_rtsp_url", "sub_rtsp_url"):
                url = (tpl.get(key) or "").strip()
                if url and url not in fallbacks:
                    fallbacks.append(url)
        return {
            "protocol": protocol,
            "main_channel": spec["main_channel"],
            "sub_channel": spec["sub_channel"],
            "recording_channel": spec["sub_channel"],
            "main_rtsp_url": main_url,
            "sub_rtsp_url": sub_url,
            "rtsp_source": "auto_sparsh",
            "fallback_urls": fallbacks,
        }

    main_url = _assemble_url(
        scheme="rtsp",
        ip=ip,
        port=port,
        path=spec["main_path"],
        username=username,
        password=password,
    )
    sub_url = _assemble_url(
        scheme="rtsp",
        ip=ip,
        port=port,
        path=spec["sub_path"],
        username=username,
        password=password,
    )

    fallback_urls = []
    for path in spec.get("fallback_paths") or []:
        url = _assemble_url(
            scheme="rtsp",
            ip=ip,
            port=port,
            path=path,
            username=username,
            password=password,
        )
        if url and url not in fallback_urls:
            fallback_urls.append(url)

    return {
        "protocol": protocol,
        "main_channel": spec["main_channel"],
        "sub_channel": spec["sub_channel"],
        "recording_channel": spec["sub_channel"],
        "main_rtsp_url": main_url,
        "sub_rtsp_url": sub_url,
        "rtsp_source": spec["rtsp_source"],
        "fallback_urls": fallback_urls,
        "browser_transcode_h264": bool(spec.get("browser_transcode_h264")),
    }


def build_rtsp_path(
    model: str,
    channel: str = "102",
    *,
    main: bool = False,
) -> str:
    """Legacy path helper — delegates to brand templates."""
    brand = normalize_make(model)
    if "dahua" in (model or "").lower() and brand not in AUTO_RTSP_BRANDS:
        brand = "DAHUA"
    if "hikvision" in (model or "").lower() or "hik" in (model or "").lower():
        brand = "HIKVISION"
    spec = _brand_path_spec(brand)
    return spec["main_path"] if main else spec["sub_path"]


def build_rtsp_url(
    *,
    ip_address: str,
    port: int,
    username: str,
    password: str,
    model: str = "",
    channel: str = "102",
    main: bool = False,
) -> str:
    """Legacy single-URL builder."""
    make = normalize_make(model or "HIKVISION")
    built = build_rtsp_urls(
        make=make,
        ip=ip_address,
        username=username,
        password=password,
        port=port,
    )
    return built["main_rtsp_url"] if main else built["sub_rtsp_url"]


def rewrite_rtsp_credentials(rtsp_url: str, username: str, password: str) -> str:
    """Replace user:pass in an RTSP/ONVIF URL, keeping host/path unchanged."""
    if not rtsp_url or "://" not in rtsp_url:
        return rtsp_url
    scheme, rest = rtsp_url.split("://", 1)
    if "@" not in rest:
        return rtsp_url
    _, host_path = rest.split("@", 1)
    user, pwd = _encode_credentials(username, password)
    return f"{scheme}://{user}:{pwd}@{host_path}"


def _legacy_sub_url(camera_doc: dict) -> str:
    """Read sub URL from canonical field or legacy rtsp_url (not written back)."""
    return (camera_doc.get("sub_rtsp_url") or camera_doc.get("rtsp_url") or "").strip()


def _is_auto_brand(protocol: str) -> bool:
    return normalize_make(protocol) in AUTO_RTSP_BRANDS


def rtsp_url_credentials_stale(camera_doc: dict) -> bool:
    """True when stored RTSP URLs embed a different user/pass than the camera doc."""
    password = str(camera_doc.get("password") or "")
    username = (camera_doc.get("username") or "admin").strip()
    for key in ("main_rtsp_url", "sub_rtsp_url"):
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
    """Rebuild auto-brand URLs or refresh credentials in manual URLs."""
    doc = dict(camera_doc)
    protocol = normalize_make(doc.get("protocol") or "HIKVISION")
    if protocol in ("ONVIF", "CUSTOM"):
        username = (doc.get("username") or "admin").strip()
        password = doc.get("password") or ""
        for key in ("main_rtsp_url", "sub_rtsp_url"):
            if doc.get(key):
                doc[key] = rewrite_rtsp_credentials(doc[key], username, password)
        sub = _legacy_sub_url(doc)
        if sub and not doc.get("sub_rtsp_url"):
            doc["sub_rtsp_url"] = sub
        if not doc.get("main_rtsp_url") and doc.get("sub_rtsp_url"):
            doc["main_rtsp_url"] = doc["sub_rtsp_url"]
        doc["rtsp_url_source"] = doc.get("rtsp_url_source") or (
            "onvif" if protocol == "ONVIF" else "custom"
        )
        return doc
    return apply_rtsp_urls(doc, force_auto=True)


def apply_rtsp_urls(camera_doc: dict, *, force_auto: bool = False) -> dict:
    """Set main/sub RTSP URL fields from protocol/brand."""
    doc = dict(camera_doc)
    protocol = normalize_make(doc.get("protocol") or "HIKVISION")
    if protocol in ("ONVIF", "CUSTOM") and not force_auto:
        sub = _legacy_sub_url(doc)
        if sub and not doc.get("sub_rtsp_url"):
            doc["sub_rtsp_url"] = sub
        doc["rtsp_url_source"] = doc.get("rtsp_url_source") or (
            "onvif" if protocol == "ONVIF" else "custom"
        )
        return doc

    make = protocol if _is_auto_brand(protocol) else normalize_make(doc.get("model") or "HIKVISION")
    ip = (doc.get("ip_address") or "").strip()
    built = build_rtsp_urls(
        make=make,
        ip=ip,
        username=(doc.get("username") or "admin").strip(),
        password=doc.get("password") or "",
        port=int(doc.get("port") or 554),
    )

    doc["protocol"] = built["protocol"]
    doc["main_channel"] = built["main_channel"]
    doc["sub_channel"] = built["sub_channel"]
    doc["recording_channel"] = built["recording_channel"]
    doc["main_rtsp_url"] = built["main_rtsp_url"]
    doc["sub_rtsp_url"] = built["sub_rtsp_url"]
    doc["rtsp_url_source"] = built["rtsp_source"]
    fallbacks = built.get("fallback_urls") or []
    if fallbacks:
        doc["rtsp_fallback_urls"] = fallbacks
    elif "rtsp_fallback_urls" in doc:
        doc.pop("rtsp_fallback_urls", None)

    from app.services.camera_uid import apply_default_camera_names

    doc = apply_default_camera_names(doc)

    if not doc.get("model"):
        doc["model"] = make.title() if make != "HIKVISION" else "Hikvision"

    return doc


def effective_camera_rtsp_urls(camera_doc: dict) -> Dict[str, str]:
    """Current main/sub RTSP URLs derived from stored credentials."""
    protocol = normalize_make(camera_doc.get("protocol") or "HIKVISION")
    if protocol in ("ONVIF", "CUSTOM"):
        synced = sync_camera_rtsp_urls(camera_doc)
        sub = (synced.get("sub_rtsp_url") or _legacy_sub_url(synced)).strip()
        main = (synced.get("main_rtsp_url") or sub).strip()
        if sub:
            return {"main_rtsp_url": main, "sub_rtsp_url": sub}
    urls = build_camera_rtsp_urls(camera_doc)
    return {
        "main_rtsp_url": urls.get("main_rtsp_url", ""),
        "sub_rtsp_url": urls.get("sub_rtsp_url", ""),
    }


def build_camera_rtsp_urls(camera_doc: dict) -> Dict[str, str]:
    """main → fullscreen/recording, sub → grid/live."""
    make = normalize_make(
        camera_doc.get("protocol") or camera_doc.get("model") or "HIKVISION"
    )
    if not _is_auto_brand(make):
        make = normalize_make(camera_doc.get("model") or "HIKVISION")

    built = build_rtsp_urls(
        make=make,
        ip=(camera_doc.get("ip_address") or "").strip(),
        username=(camera_doc.get("username") or "admin").strip(),
        password=camera_doc.get("password") or "",
        port=int(camera_doc.get("port") or 554),
    )
    return {
        "main_rtsp_url": built["main_rtsp_url"],
        "sub_rtsp_url": built["sub_rtsp_url"],
        "recording_channel": built["recording_channel"],
        "main_channel": built["main_channel"],
        "sub_channel": built["sub_channel"],
        "rtsp_url_source": built["rtsp_source"],
        "rtsp_fallback_urls": built.get("fallback_urls") or [],
    }


def _needs_browser_h264_transcode(
    camera_doc: dict,
    *,
    main: bool = False,
) -> bool:
    if camera_doc.get("go2rtc_transcode_h264") or camera_doc.get("browser_transcode_h264"):
        return True
    if main and camera_doc.get("go2rtc_transcode_main_h264"):
        return True
    if not main and camera_doc.get("go2rtc_transcode_sub_h264"):
        return True
    protocol = normalize_make(camera_doc.get("protocol") or "")
    if protocol in ("UNIVIEW", "PRAMA"):
        return True
    return bool(_brand_path_spec(protocol).get("browser_transcode_h264"))


def _go2rtc_browser_source(
    url: str,
    camera_doc: dict,
    *,
    main: bool = False,
) -> str:
    """Wrap HEVC brand streams so browsers get H.264 via go2rtc/ffmpeg."""
    text = (url or "").strip()
    if not text or text.startswith("ffmpeg:"):
        return text
    # Skip non-RTSP fallbacks and native H.264 tertiary UNV stream.
    if text.startswith("onvif://") or "/media/video3" in text:
        return text
    if _needs_browser_h264_transcode(camera_doc, main=main):
        return f"ffmpeg:{text}#video=h264#audio=copy"
    return text


def stream_source_urls(camera_doc: dict, *, main: bool = False) -> List[str]:
    """Primary URL plus brand fallbacks for go2rtc (grid=sub, fullscreen=main)."""
    urls = effective_camera_rtsp_urls(camera_doc)
    primary = (urls.get("main_rtsp_url") if main else urls.get("sub_rtsp_url") or "").strip()
    if not primary:
        return []

    # Rebuild brand fallbacks so DB-inserted cameras without
    # rtsp_fallback_urls still get ONVIF/Dahua/UNV failover sources.
    # For auto brands, ignore stale DB leftovers from a previous Make
    # (e.g. SPARSH→HIKVISION) — those burn RTSP slots and cause 453.
    built = build_camera_rtsp_urls(camera_doc)
    protocol = normalize_make(camera_doc.get("protocol") or "HIKVISION")
    if protocol in ("ONVIF", "CUSTOM"):
        fallbacks = list(camera_doc.get("rtsp_fallback_urls") or [])
    else:
        fallbacks = list(built.get("rtsp_fallback_urls") or [])

    out = [_go2rtc_browser_source(primary, camera_doc, main=main)]
    for url in fallbacks:
        u = (url or "").strip()
        if not u:
            continue
        wrapped = _go2rtc_browser_source(u, camera_doc, main=main)
        if wrapped and wrapped not in out:
            out.append(wrapped)
    return out


def mask_rtsp_url(rtsp_url: str) -> str:
    if "@" not in rtsp_url:
        return rtsp_url
    return rtsp_url.split("://")[0] + "://" + rtsp_url.split("@", 1)[-1]
