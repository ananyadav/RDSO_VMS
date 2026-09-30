"""ONVIF Profile S — media profiles + GetStreamUri RTSP resolution (RDSO 18.3.15)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from app.services.onvif_media import (
    _get_profiles,
    _media_soap,
    assign_main_sub_profiles,
    parse_profiles_list,
)
from app.services.onvif_ptz import _TRT, _TT, _error_from_response, _local, _xml_escape
from app.services.rtsp_utils import ensure_rtsp_credentials, mask_rtsp_url, normalize_make

logger = logging.getLogger(__name__)

STREAM_SOURCE_ONVIF = "onvif_getstreamuri"


def parse_stream_uri_response(text: str) -> Optional[str]:
    """Extract MediaUri/Uri from GetStreamUriResponse."""
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for node in root.iter():
        if _local(node.tag) != "Uri":
            continue
        uri = (node.text or "").strip()
        if uri.lower().startswith(("rtsp://", "rtsps://")):
            return uri
    return None


async def get_onvif_stream_uri(
    camera: dict,
    profile_token: str,
    *,
    stream_protocol: str = "RTSP",
) -> Tuple[Optional[str], Optional[str]]:
    """Call ONVIF Media GetStreamUri for one profile token.

    Returns (uri, error). Uri may be without credentials; caller should inject.
    """
    token = (profile_token or "").strip()
    if not token:
        return None, "profile_token required"

    body = (
        f'<trt:GetStreamUri xmlns:trt="{_TRT}" xmlns:tt="{_TT}">'
        "<trt:StreamSetup>"
        f"<tt:Stream>RTP-Unicast</tt:Stream>"
        "<tt:Transport>"
        f"<tt:Protocol>{_xml_escape(stream_protocol)}</tt:Protocol>"
        "</tt:Transport>"
        "</trt:StreamSetup>"
        f"<trt:ProfileToken>{_xml_escape(token)}</trt:ProfileToken>"
        "</trt:GetStreamUri>"
    )
    status, text, _url = await _media_soap(camera, f"{_TRT}/GetStreamUri", body)
    if status not in (200, 201) or "Fault" in text:
        return None, _error_from_response(status, text)
    uri = parse_stream_uri_response(text)
    if not uri:
        return None, "GetStreamUri returned no RTSP Uri"
    return uri, None


def _with_camera_credentials(camera: dict, uri: str) -> str:
    return ensure_rtsp_credentials(
        uri,
        (camera.get("username") or "admin").strip(),
        camera.get("password") or "",
    )


def public_stream_block(role: str, info: Optional[dict], uri: Optional[str], err: Optional[str]) -> dict:
    return {
        "role": role,
        "profile_token": (info or {}).get("profile_token"),
        "name": (info or {}).get("name"),
        "encoding": (info or {}).get("encoding"),
        "width": (info or {}).get("width"),
        "height": (info or {}).get("height"),
        "fps": (info or {}).get("fps"),
        "rtsp_uri": uri,
        "rtsp_uri_masked": mask_rtsp_url(uri) if uri else None,
        "ok": bool(uri) and not err,
        "error": err,
    }


async def resolve_onvif_profile_s_streams(camera: dict) -> Dict[str, Any]:
    """Discover media profiles and resolve main/sub RTSP via GetStreamUri.

    Vendor-neutral Profile S path — not Hikvision-specific.
    """
    camera_id = str(camera.get("_id") or camera.get("id") or "")
    result: Dict[str, Any] = {
        "camera_id": camera_id,
        "profile": "S",
        "supported": False,
        "message": "",
        "profiles": [],
        "main": None,
        "sub": None,
        "source": STREAM_SOURCE_ONVIF,
    }

    profiles, err = await _get_profiles(camera)
    if err:
        result["message"] = err
        return result
    if not profiles:
        result["message"] = "ONVIF camera returned no media profiles"
        return result

    result["profiles"] = [
        {
            "profile_token": p.get("profile_token"),
            "name": p.get("name"),
            "encoding": p.get("encoding"),
            "width": p.get("width"),
            "height": p.get("height"),
            "fps": p.get("fps"),
        }
        for p in profiles
    ]

    main_info, sub_info = assign_main_sub_profiles(profiles)
    if not main_info or not main_info.get("profile_token"):
        result["message"] = "No ONVIF media profile suitable for streaming"
        return result

    main_uri, main_err = await get_onvif_stream_uri(camera, main_info["profile_token"])
    if main_uri:
        main_uri = _with_camera_credentials(camera, main_uri)
    result["main"] = public_stream_block("main", main_info, main_uri, main_err)

    sub_uri = sub_err = None
    if sub_info and sub_info.get("profile_token"):
        sub_uri, sub_err = await get_onvif_stream_uri(camera, sub_info["profile_token"])
        if sub_uri:
            sub_uri = _with_camera_credentials(camera, sub_uri)
        result["sub"] = public_stream_block("sub", sub_info, sub_uri, sub_err)
    else:
        result["sub"] = public_stream_block(
            "sub", None, None, "No secondary media profile on device"
        )

    if main_uri:
        result["supported"] = True
        result["message"] = (
            "ONVIF Profile S GetStreamUri resolved"
            + (" (main+sub)" if sub_uri else " (main only)")
        )
    else:
        result["message"] = main_err or "GetStreamUri failed for main profile"
    return result


def should_resolve_onvif_for_recording(camera: dict) -> bool:
    """True when recording should prefer ONVIF-resolved RTSP over brand templates."""
    protocol = normalize_make(camera.get("protocol") or "")
    source = str(camera.get("rtsp_url_source") or "").strip().lower()
    if protocol == "ONVIF":
        return True
    if source in (STREAM_SOURCE_ONVIF, "onvif", "onvif_getstreamuri"):
        return True
    if protocol == "CUSTOM" and not (camera.get("main_rtsp_url") or camera.get("sub_rtsp_url")):
        return True
    return False


async def ensure_onvif_recording_urls(camera: dict) -> Dict[str, Any]:
    """Resolve GetStreamUri into camera main/sub RTSP fields for recording use.

    Mutates a copy; does not persist. Caller may persist when requested.
    """
    doc = dict(camera)
    if not should_resolve_onvif_for_recording(doc):
        return {
            "ok": True,
            "applied": False,
            "camera": doc,
            "message": "Brand/manual RTSP path retained (ONVIF resolve not required)",
        }

    resolved = await resolve_onvif_profile_s_streams(doc)
    if not resolved.get("supported"):
        # Keep any existing manual URLs; report honesty
        has_existing = bool(doc.get("main_rtsp_url") or doc.get("sub_rtsp_url"))
        return {
            "ok": has_existing,
            "applied": False,
            "camera": doc,
            "resolve": resolved,
            "message": resolved.get("message")
            or "ONVIF Profile S stream URI unavailable",
        }

    main = (resolved.get("main") or {}).get("rtsp_uri")
    sub = (resolved.get("sub") or {}).get("rtsp_uri")
    if main:
        doc["main_rtsp_url"] = main
    if sub:
        doc["sub_rtsp_url"] = sub
    elif main and not doc.get("sub_rtsp_url"):
        doc["sub_rtsp_url"] = main
    doc["rtsp_url_source"] = STREAM_SOURCE_ONVIF
    doc["onvif_profile_s"] = {
        "resolved": True,
        "main_token": (resolved.get("main") or {}).get("profile_token"),
        "sub_token": (resolved.get("sub") or {}).get("profile_token"),
    }
    return {
        "ok": True,
        "applied": True,
        "camera": doc,
        "resolve": resolved,
        "message": resolved.get("message"),
    }


async def probe_onvif_profile_s(camera: dict) -> Dict[str, Any]:
    """Capability-style probe used by interop summary (no fake success)."""
    resolved = await resolve_onvif_profile_s_streams(camera)
    return {
        "profile": "S",
        "supported": bool(resolved.get("supported")),
        "message": resolved.get("message") or "",
        "main_ok": bool((resolved.get("main") or {}).get("ok")),
        "sub_ok": bool((resolved.get("sub") or {}).get("ok")),
        "profile_count": len(resolved.get("profiles") or []),
        "source": STREAM_SOURCE_ONVIF,
    }


# Re-export for tests that parse profiles without network
__all__ = [
    "STREAM_SOURCE_ONVIF",
    "parse_stream_uri_response",
    "get_onvif_stream_uri",
    "resolve_onvif_profile_s_streams",
    "ensure_onvif_recording_urls",
    "should_resolve_onvif_for_recording",
    "probe_onvif_profile_s",
    "parse_profiles_list",
]
