"""ONVIF Media encoder profile read/write for stream profile (RDSO 18.1.7)."""

from __future__ import annotations

import copy
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from app.services.onvif_ptz import (
    _MEDIA_PATHS,
    _TRT,
    _TT,
    _camera_key,
    _error_from_response,
    _local,
    _service_urls,
    _soap_with_auth,
    _try_urls,
    _xml_escape,
)

logger = logging.getLogger(__name__)

MIN_FPS = 1
MAX_FPS = 25


def _clamp_fps(value: float | int | None) -> Optional[int]:
    if value is None:
        return None
    try:
        fps = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(MIN_FPS, min(MAX_FPS, fps))


def _child_text(node: ET.Element, local_name: str) -> Optional[str]:
    for child in node:
        if _local(child.tag) == local_name and (child.text or "").strip():
            return (child.text or "").strip()
    return None


def _find_first(node: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in node.iter():
        if _local(child.tag) == local_name:
            return child
    return None


def _set_text(node: ET.Element, local_name: str, value: str) -> bool:
    target = _find_first(node, local_name)
    if target is None:
        return False
    target.text = value
    return True


async def _media_soap(camera: dict, action: str, body: str) -> Tuple[int, str, str]:
    status, text, url = await _try_urls(
        camera,
        _service_urls(camera, None, _MEDIA_PATHS),
        action,
        body,
    )
    return status, text, url


def parse_profiles_list(text: str) -> List[Dict[str, Any]]:
    """Parse GetProfiles response into profile + encoder token entries."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    profiles: List[Dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) != "Profiles":
            continue
        profile_token = (node.attrib.get("token") or "").strip()
        name = _child_text(node, "Name") or profile_token or "Profile"
        encoder_token = ""
        encoding = None
        width = height = fps = None

        for child in node.iter():
            if _local(child.tag) != "VideoEncoderConfiguration":
                continue
            encoder_token = (child.attrib.get("token") or encoder_token or "").strip()
            encoding = _child_text(child, "Encoding") or encoding
            res = _find_first(child, "Resolution")
            if res is not None:
                width = _child_text(res, "Width")
                height = _child_text(res, "Height")
            rate = _find_first(child, "RateControl")
            if rate is not None:
                fps = _child_text(rate, "FrameRateLimit")

        if not encoder_token:
            for child in node.iter():
                if _local(child.tag) == "VideoEncoderConfiguration" and child.attrib.get("token"):
                    encoder_token = child.attrib["token"].strip()
                    break

        profiles.append(
            {
                "profile_token": profile_token,
                "name": name,
                "encoder_token": encoder_token,
                "encoding": encoding,
                "width": int(width) if width and width.isdigit() else None,
                "height": int(height) if height and height.isdigit() else None,
                "fps": _clamp_fps(float(fps)) if fps else None,
            }
        )
    return profiles


def parse_encoder_configuration(text: str) -> Tuple[Optional[str], Dict[str, Any], Optional[ET.Element]]:
    """Returns (encoder_token, fields, Configuration element for Set)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, {}, None

    config = None
    for node in root.iter():
        if _local(node.tag) == "Configuration":
            config = node
            break

    if config is None:
        return None, {}, None

    token = (config.attrib.get("token") or "").strip()
    encoding = _child_text(config, "Encoding")
    res = _find_first(config, "Resolution")
    width = _child_text(res, "Width") if res is not None else None
    height = _child_text(res, "Height") if res is not None else None
    rate = _find_first(config, "RateControl")
    fps = _child_text(rate, "FrameRateLimit") if rate is not None else None

    fields = {
        "encoding": encoding,
        "width": int(width) if width and str(width).isdigit() else None,
        "height": int(height) if height and str(height).isdigit() else None,
        "fps": _clamp_fps(float(fps)) if fps else None,
    }

    return token or None, fields, copy.deepcopy(config)


def parse_encoder_options(text: str) -> Dict[str, Any]:
    """Extract resolution and FPS options from GetVideoEncoderConfigurationOptions."""
    resolutions: List[Dict[str, int]] = []
    fps_min: Optional[int] = None
    fps_max: Optional[int] = None

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"resolutions": [], "fps_options": [], "fps_supported": False, "res_supported": False}

    for node in root.iter():
        ln = _local(node.tag)
        if ln == "ResolutionsAvailable":
            w = _child_text(node, "Width")
            h = _child_text(node, "Height")
            if w and h and w.isdigit() and h.isdigit():
                resolutions.append({"width": int(w), "height": int(h)})
        elif ln == "FrameRateRange":
            lo = _child_text(node, "Min")
            hi = _child_text(node, "Max")
            if lo:
                fps_min = _clamp_fps(float(lo))
            if hi:
                fps_max = _clamp_fps(float(hi))

    seen = set()
    unique_res: List[Dict[str, int]] = []
    for r in sorted(resolutions, key=lambda x: -(x["width"] * x["height"])):
        key = (r["width"], r["height"])
        if key not in seen:
            seen.add(key)
            unique_res.append(r)

    fps_options: List[int] = []
    if fps_min is not None and fps_max is not None:
        fps_options = list(range(fps_min, fps_max + 1))
    fps_supported = bool(fps_options)

    return {
        "resolutions": unique_res,
        "fps_options": fps_options,
        "fps_supported": fps_supported,
        "res_supported": bool(unique_res),
    }


def _profile_role(name: str) -> Optional[str]:
    n = (name or "").lower()
    if any(k in n for k in ("sub", "second", "minor", "low", "stream2", "profile_2", "profile2")):
        return "sub"
    if any(k in n for k in ("main", "primary", "first", "high", "stream1", "profile_1", "profile1")):
        return "main"
    return None


def assign_main_sub_profiles(profiles: List[Dict[str, Any]]) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Map ONVIF media profiles to main/sub stream blocks."""
    video = [p for p in profiles if p.get("encoder_token")]
    if not video:
        return None, None
    if len(video) == 1:
        return video[0], None

    main_match = next((p for p in video if _profile_role(p.get("name") or "") == "main"), None)
    sub_match = next((p for p in video if _profile_role(p.get("name") or "") == "sub"), None)
    if main_match and sub_match:
        return main_match, sub_match

    sorted_by_res = sorted(
        video,
        key=lambda p: -((p.get("width") or 0) * (p.get("height") or 0)),
    )
    main = sorted_by_res[0]
    sub = sorted_by_res[-1] if sorted_by_res[-1] is not main else (
        sorted_by_res[1] if len(sorted_by_res) > 1 else None
    )
    return main, sub


def _onvif_label(profile: str, info: Dict[str, Any]) -> str:
    name = (info.get("name") or info.get("profile_token") or profile).strip()
    token = (info.get("encoder_token") or info.get("profile_token") or "").strip()
    if token and token != name:
        return f"{name} ({token})"
    return name or profile.capitalize()


async def _get_profiles(camera: dict) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    body = f'<trt:GetProfiles xmlns:trt="{_TRT}"/>'
    status, text, _url = await _media_soap(camera, f"{_TRT}/GetProfiles", body)
    if status not in (200, 201) or "Fault" in text:
        return [], _error_from_response(status, text)
    profiles = parse_profiles_list(text)
    if not profiles:
        return [], "ONVIF camera returned no media profiles"
    return profiles, None


async def _get_encoder_config(camera: dict, encoder_token: str) -> Tuple[Optional[str], Dict[str, Any], Optional[ET.Element], Optional[str]]:
    body = (
        f'<trt:GetVideoEncoderConfiguration xmlns:trt="{_TRT}">'
        f"<trt:ConfigurationToken>{_xml_escape(encoder_token)}</trt:ConfigurationToken>"
        f"</trt:GetVideoEncoderConfiguration>"
    )
    status, text, _url = await _media_soap(camera, f"{_TRT}/GetVideoEncoderConfiguration", body)
    if status not in (200, 201) or "Fault" in text:
        return None, {}, None, _error_from_response(status, text)
    token, fields, config_elem = parse_encoder_configuration(text)
    return token, fields, config_elem, None


def _configuration_to_set_xml(token: str, config_elem: ET.Element) -> str:
    inner = ET.tostring(config_elem, encoding="unicode")
    inner = re.sub(r'\sxmlns:ns\d+="[^"]+"', "", inner)
    inner = re.sub(r"ns\d+:", "tt:", inner)
    inner = re.sub(r"^<[^>]+>", "", inner)
    inner = re.sub(r"</[^>]+>$", "", inner)
    return f'<trt:Configuration token="{_xml_escape(token)}">{inner}</trt:Configuration>'


async def _get_encoder_options(
    camera: dict,
    encoder_token: str,
    profile_token: str,
) -> Dict[str, Any]:
    body = (
        f'<trt:GetVideoEncoderConfigurationOptions xmlns:trt="{_TRT}">'
        f"<trt:ConfigurationToken>{_xml_escape(encoder_token)}</trt:ConfigurationToken>"
        f"<trt:ProfileToken>{_xml_escape(profile_token)}</trt:ProfileToken>"
        f"</trt:GetVideoEncoderConfigurationOptions>"
    )
    status, text, _url = await _media_soap(camera, f"{_TRT}/GetVideoEncoderConfigurationOptions", body)
    if status not in (200, 201) or "Fault" in text:
        return {"resolutions": [], "fps_options": [], "fps_supported": False, "res_supported": False}
    return parse_encoder_options(text)


def _build_onvif_block(
    profile: str,
    info: Dict[str, Any],
    fields: Dict[str, Any],
    options: Dict[str, Any],
    *,
    read_error: Optional[str] = None,
) -> Dict[str, Any]:
    channel = info.get("encoder_token") or info.get("profile_token") or profile
    label = _onvif_label(profile, info)

    if read_error or not info.get("encoder_token"):
        return {
            "profile": profile,
            "channel": channel,
            "label": label,
            "supported": False,
            "message": read_error or "Encoder configuration not exposed via ONVIF",
            "current": None,
            "capabilities": None,
        }

    width = fields.get("width") or info.get("width")
    height = fields.get("height") or info.get("height")
    fps = fields.get("fps") if fields.get("fps") is not None else info.get("fps")
    codec = fields.get("encoding") or info.get("encoding")

    res_options = list(options.get("resolutions") or [])
    if width and height and not any(o["width"] == width and o["height"] == height for o in res_options):
        res_options.insert(0, {"width": int(width), "height": int(height)})

    fps_options = list(options.get("fps_options") or [])
    if fps is not None and fps not in fps_options:
        fps_options.append(int(fps))
    fps_options = sorted(set(f for f in fps_options if MIN_FPS <= f <= MAX_FPS))

    if not fps_options and fps is not None:
        fps_options = [int(fps)]

    res_supported = bool(options.get("res_supported") and res_options)
    fps_supported = bool(options.get("fps_supported") and fps_options)

    if not res_options and not fps_options:
        return {
            "profile": profile,
            "channel": channel,
            "label": label,
            "supported": False,
            "message": "Camera does not expose encoder configuration through ONVIF",
            "current": None,
            "capabilities": None,
        }

    return {
        "profile": profile,
        "channel": channel,
        "label": label,
        "supported": True,
        "message": None,
        "current": {
            "fps": float(fps) if fps is not None else None,
            "width": width,
            "height": height,
            "codec": codec,
            "resolution": f"{width}x{height}" if width and height else None,
        },
        "capabilities": {
            "fps": {
                "supported": fps_supported,
                "options": fps_options,
                "min": MIN_FPS,
                "max": MAX_FPS,
            },
            "resolution": {
                "supported": res_supported,
                "options": res_options,
            },
        },
    }


async def _read_onvif_stream(camera: dict, info: Dict[str, Any], profile: str) -> Dict[str, Any]:
    encoder_token = info.get("encoder_token") or ""
    profile_token = info.get("profile_token") or ""
    if not encoder_token:
        return _build_onvif_block(profile, info, {}, {}, read_error="No video encoder on profile")

    _token, fields, _config_elem, err = await _get_encoder_config(camera, encoder_token)
    if err:
        return _build_onvif_block(profile, info, {}, {}, read_error=err)

    options = await _get_encoder_options(camera, encoder_token, profile_token)
    return _build_onvif_block(profile, info, fields, options)


async def get_onvif_stream_profile(camera: dict, base: Dict[str, Any]) -> Dict[str, Any]:
    profiles, err = await _get_profiles(camera)
    if err:
        base["supported"] = False
        base["message"] = "Not supported"
        base["main"] = _build_onvif_block("main", {}, {}, {}, read_error="Not supported")
        base["sub"] = _build_onvif_block("sub", {}, {}, {}, read_error="Not supported")
        return base

    main_info, sub_info = assign_main_sub_profiles(profiles)
    if not main_info:
        base["supported"] = False
        base["message"] = "Not supported"
        base["main"] = _build_onvif_block("main", {}, {}, {}, read_error="Not supported")
        base["sub"] = _build_onvif_block("sub", {}, {}, {}, read_error="Not supported")
        return base

    base["driver"] = "onvif_media"
    base["main"] = await _read_onvif_stream(camera, main_info, "main")
    base["sub"] = (
        await _read_onvif_stream(camera, sub_info, "sub")
        if sub_info
        else {
            "profile": "sub",
            "channel": "",
            "label": "Sub",
            "supported": False,
            "message": "No sub stream profile on camera",
            "current": None,
            "capabilities": None,
        }
    )
    base["supported"] = bool(base["main"].get("supported") or base["sub"].get("supported"))
    base["message"] = None if base["supported"] else "Not supported"
    return base


async def _apply_onvif_profile(camera: dict, profile: str, block: dict) -> Dict[str, Any]:
    profiles, err = await _get_profiles(camera)
    if err:
        return {"ok": False, "error": err}

    main_info, sub_info = assign_main_sub_profiles(profiles)
    info = main_info if profile == "main" else sub_info
    if not info or not info.get("encoder_token"):
        return {"ok": False, "error": f"No ONVIF {profile} encoder profile"}

    encoder_token = info["encoder_token"]
    token, before_fields, config_elem, read_err = await _get_encoder_config(camera, encoder_token)
    if read_err or config_elem is None or not token:
        return {"ok": False, "error": read_err or "Could not read encoder configuration"}

    options = await _get_encoder_options(camera, encoder_token, info.get("profile_token") or "")
    changes: Dict[str, Any] = {}

    if "fps" in block and block["fps"] is not None:
        try:
            fps = int(block["fps"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid fps"}
        if fps < MIN_FPS or fps > MAX_FPS:
            return {"ok": False, "error": f"FPS must be between {MIN_FPS} and {MAX_FPS}"}
        allowed_fps = options.get("fps_options") or []
        if allowed_fps and fps not in allowed_fps:
            return {"ok": False, "error": f"FPS {fps} not in camera-supported options"}
        changes["fps"] = fps

    width = block.get("width")
    height = block.get("height")
    if width is not None and height is not None:
        w, h = int(width), int(height)
        allowed = options.get("resolutions") or []
        if allowed and not any(o["width"] == w and o["height"] == h for o in allowed):
            return {"ok": False, "error": f"Resolution {w}x{h} not in camera-supported options"}
        changes["width"] = w
        changes["height"] = h

    if not changes:
        return {"ok": False, "error": "No changes requested"}

    if "fps" in changes:
        rate = _find_first(config_elem, "RateControl")
        if rate is None or not _set_text(rate, "FrameRateLimit", str(changes["fps"])):
            return {"ok": False, "error": "FPS not supported on this stream"}
    if "width" in changes and "height" in changes:
        res = _find_first(config_elem, "Resolution")
        if res is None:
            return {"ok": False, "error": "Resolution not supported on this stream"}
        _set_text(res, "Width", str(changes["width"]))
        _set_text(res, "Height", str(changes["height"]))

    set_config = _configuration_to_set_xml(token, config_elem)
    body = (
        f'<trt:SetVideoEncoderConfiguration xmlns:trt="{_TRT}" xmlns:tt="{_TT}">'
        f"{set_config}"
        f"<trt:ForcePersistence>true</trt:ForcePersistence>"
        f"</trt:SetVideoEncoderConfiguration>"
    )
    status, text, _url = await _media_soap(camera, f"{_TRT}/SetVideoEncoderConfiguration", body)
    if status not in (200, 201, 204) or "Fault" in text:
        return {"ok": False, "error": _error_from_response(status, text)}

    _after_token, after_fields, _, _ = await _get_encoder_config(camera, encoder_token)
    return {
        "ok": True,
        "channel": encoder_token,
        "before": {
            "fps": before_fields.get("fps"),
            "width": before_fields.get("width"),
            "height": before_fields.get("height"),
        },
        "after": {
            "fps": after_fields.get("fps"),
            "width": after_fields.get("width"),
            "height": after_fields.get("height"),
        },
        "changes": changes,
    }


async def apply_onvif_stream_profile(camera: dict, payload: dict) -> Dict[str, Any]:
    results: Dict[str, Any] = {"ok": True, "cameraId": str(camera.get("_id")), "applied": {}}
    for profile in ("main", "sub"):
        block = payload.get(profile)
        if not block or not isinstance(block, dict):
            continue
        try:
            results["applied"][profile] = await _apply_onvif_profile(camera, profile, block)
        except Exception as exc:
            logger.warning("[stream-profile-onvif] apply %s failed: %s", _camera_key(camera), exc)
            results["applied"][profile] = {"ok": False, "error": str(exc)}
            results["ok"] = False
    return results
