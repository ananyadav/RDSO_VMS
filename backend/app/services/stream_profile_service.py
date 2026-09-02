"""Per-camera stream encoder profile (FPS / resolution) via ISAPI or ONVIF."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection
from app.services.hikvision_ptz import _isapi
from app.services.rtsp_utils import normalize_make

logger = logging.getLogger(__name__)

# Brands that expose Hikvision-style ISAPI streaming channels.
ISAPI_STREAM_BRANDS = frozenset({"HIKVISION", "PRAMA", "HONEYWELL", "SPARSH", "HIK"})

# Fail fast when probing ONVIF on offline/non-ONVIF cameras.
ONVIF_PROFILE_TIMEOUT_SEC = 6.0

ENCODER_TAGS = (
    "videoCodecType",
    "videoResolutionWidth",
    "videoResolutionHeight",
    "maxFrameRate",
    "GovLength",
    "keyFrameInterval",
)

MIN_FPS = 1
MAX_FPS = 25


def uses_isapi_stream_profile(protocol: str | None) -> bool:
    return normalize_make(protocol or "") in ISAPI_STREAM_BRANDS


def resolve_stream_driver(protocol: str | None) -> str:
    """ISAPI for Hikvision-family; all other protocols try ONVIF Media."""
    if uses_isapi_stream_profile(protocol):
        return "isapi"
    return "onvif"


def supports_stream_profile(protocol: str | None) -> bool:
    """All cameras attempt ISAPI or ONVIF at runtime; unsupported ones return Not supported."""
    return True


def _onvif_unsupported_result(base: Dict[str, Any], message: str = "Not supported") -> Dict[str, Any]:
    base["supported"] = False
    base["message"] = message
    base["driver"] = "onvif_media"
    base["main"] = _unsupported_block("main", "", message)
    base["sub"] = _unsupported_block("sub", "", message)
    return base


async def _try_onvif_stream_profile(camera: dict, base: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.onvif_media import get_onvif_stream_profile

    try:
        return await asyncio.wait_for(
            get_onvif_stream_profile(camera, base),
            timeout=ONVIF_PROFILE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning("[stream-profile] ONVIF read timed out for %s", base.get("cameraId"))
        return _onvif_unsupported_result(base, "Not supported")
    except Exception as exc:
        logger.warning("[stream-profile] ONVIF read failed for %s: %s", base.get("cameraId"), exc)
        return _onvif_unsupported_result(base, "Not supported")


async def _try_onvif_apply(camera: dict, camera_id: str, payload: dict) -> Dict[str, Any]:
    from app.services.onvif_media import apply_onvif_stream_profile

    try:
        result = await asyncio.wait_for(
            apply_onvif_stream_profile(camera, payload),
            timeout=ONVIF_PROFILE_TIMEOUT_SEC,
        )
        result["cameraId"] = camera_id
        return result
    except asyncio.TimeoutError:
        logger.warning("[stream-profile] ONVIF apply timed out for %s", camera_id)
        return {"ok": False, "error": "Not supported", "cameraId": camera_id}
    except Exception as exc:
        logger.warning("[stream-profile] ONVIF apply failed for %s: %s", camera_id, exc)
        return {"ok": False, "error": "Not supported", "cameraId": camera_id}


def _channel_id(camera: dict, profile: str) -> str:
    if profile == "main":
        return str(camera.get("main_channel") or "101").strip() or "101"
    rc = (camera.get("recording_channel") or "").strip().lower()
    if rc not in ("main", "sub") and camera.get("recording_channel"):
        return str(camera.get("sub_channel") or camera.get("recording_channel") or "102").strip() or "102"
    return str(camera.get("sub_channel") or "102").strip() or "102"


def hik_fps_from_api(value: str | None) -> Optional[float]:
    if not value:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v >= 100:
        return round(v / 100.0, 2)
    return v


def hik_fps_to_api(fps: int | float) -> str:
    fps_i = int(round(float(fps)))
    fps_i = max(MIN_FPS, min(MAX_FPS, fps_i))
    return str(fps_i * 100)


def parse_encoder_fields(xml: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for tag in ENCODER_TAGS:
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml or "")
        out[tag] = m.group(1) if m else None
    return out


def replace_tag(xml: str, tag: str, value: str) -> str:
    pattern = rf"(<{tag}>)([^<]*)(</{tag}>)"
    if not re.search(pattern, xml):
        raise ValueError(f"tag <{tag}> not found")
    return re.sub(pattern, rf"\g<1>{value}\g<3>", xml, count=1)


def _parse_numeric_opts(cap_xml: str, tag: str) -> List[int]:
    values: List[int] = []
    for m in re.finditer(rf"<{tag}[^>]*opt=\"([^\"]+)\"", cap_xml or ""):
        for part in re.split(r"[,|]", m.group(1)):
            part = part.strip()
            if part.isdigit():
                values.append(int(part))
    for m in re.finditer(rf"<{tag}[^>]*min=\"(\d+)\"[^>]*max=\"(\d+)\"", cap_xml or ""):
        lo, hi = int(m.group(1)), int(m.group(2))
        if tag == "maxFrameRate":
            lo_fps = hik_fps_from_api(str(lo)) or MIN_FPS
            hi_fps = hik_fps_from_api(str(hi)) or MAX_FPS
            values.extend(range(int(lo_fps), int(hi_fps) + 1))
        else:
            values.extend([lo, hi])
    return sorted(set(values))


def _resolution_options(cap_xml: str, current_w: Optional[str], current_h: Optional[str]) -> List[Dict[str, int]]:
    widths = _parse_numeric_opts(cap_xml, "videoResolutionWidth")
    heights = _parse_numeric_opts(cap_xml, "videoResolutionHeight")
    options: List[Dict[str, int]] = []
    if widths and heights:
        for w in widths:
            for h in heights:
                if w >= 160 and h >= 120:
                    options.append({"width": w, "height": h})
    # Common Hik sub/main pairs when capabilities omit enumerations.
    if not options:
        for w, h in (
            (3840, 2160),
            (2560, 1440),
            (1920, 1080),
            (1280, 720),
            (704, 576),
            (640, 480),
            (640, 360),
            (352, 288),
        ):
            options.append({"width": w, "height": h})
    cur_w = int(current_w) if current_w and str(current_w).isdigit() else None
    cur_h = int(current_h) if current_h and str(current_h).isdigit() else None
    if cur_w and cur_h and not any(o["width"] == cur_w and o["height"] == cur_h for o in options):
        options.insert(0, {"width": cur_w, "height": cur_h})
    # De-dup and sort by pixels desc
    seen = set()
    unique: List[Dict[str, int]] = []
    for o in sorted(options, key=lambda x: -(x["width"] * x["height"])):
        key = (o["width"], o["height"])
        if key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def _fps_options(cap_xml: str, current_fps: Optional[float]) -> List[int]:
    raw = _parse_numeric_opts(cap_xml, "maxFrameRate")
    fps_vals: List[int] = []
    for v in raw:
        parsed = hik_fps_from_api(str(v))
        if parsed is not None:
            fps_vals.append(int(round(parsed)))
    if not fps_vals:
        fps_vals = list(range(MIN_FPS, MAX_FPS + 1))
    else:
        fps_vals = [f for f in fps_vals if MIN_FPS <= f <= MAX_FPS]
        if not fps_vals:
            fps_vals = list(range(MIN_FPS, MAX_FPS + 1))
    if current_fps is not None:
        cur = int(round(current_fps))
        if MIN_FPS <= cur <= MAX_FPS and cur not in fps_vals:
            fps_vals.append(cur)
    return sorted(set(fps_vals))


def _stream_label(profile: str) -> str:
    return "Main (101)" if profile == "main" else "Sub (102)"


async def _read_channel(camera: dict, channel: str) -> Tuple[Optional[str], str, Optional[str]]:
    """Returns (channel_xml, capabilities_xml, error_message)."""
    path = f"/ISAPI/Streaming/channels/{channel}"
    status, xml = await _isapi(camera, "GET", path)
    if status != 200 or not xml:
        return None, "", f"ISAPI GET {path} failed (HTTP {status})"
    cap_status, cap_xml = await _isapi(camera, "GET", f"{path}/capabilities")
    cap = cap_xml if cap_status == 200 else ""
    return xml, cap, None


def _build_profile_block(
    profile: str,
    channel: str,
    xml: str,
    cap_xml: str,
    *,
    read_error: Optional[str] = None,
) -> Dict[str, Any]:
    if read_error or not xml:
        return {
            "profile": profile,
            "channel": channel,
            "label": _stream_label(profile),
            "supported": False,
            "message": read_error or "Could not read encoder settings",
            "current": None,
            "capabilities": None,
        }

    fields = parse_encoder_fields(xml)
    fps = hik_fps_from_api(fields.get("maxFrameRate"))
    width = fields.get("videoResolutionWidth")
    height = fields.get("videoResolutionHeight")
    res_options = _resolution_options(cap_xml, width, height)
    fps_options = _fps_options(cap_xml, fps)

    width_int = int(width) if width and str(width).isdigit() else None
    height_int = int(height) if height and str(height).isdigit() else None
    res_supported = bool(cap_xml and res_options)
    fps_supported = bool(re.search(r"<maxFrameRate>", xml or ""))

    return {
        "profile": profile,
        "channel": channel,
        "label": _stream_label(profile),
        "supported": True,
        "message": None,
        "current": {
            "fps": fps,
            "width": width_int,
            "height": height_int,
            "codec": fields.get("videoCodecType"),
            "resolution": f"{width_int}x{height_int}" if width_int and height_int else None,
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


async def get_camera_stream_profile(camera_id: str) -> Dict[str, Any]:
    try:
        oid = ObjectId(camera_id)
    except (InvalidId, TypeError):
        return {"ok": False, "error": "Invalid camera id"}

    camera = await camera_collection.find_one({"_id": oid})
    if not camera:
        return {"ok": False, "error": "Camera not found"}

    protocol = normalize_make(camera.get("protocol") or "")
    driver = resolve_stream_driver(protocol)
    base = {
        "ok": True,
        "cameraId": str(camera["_id"]),
        "ip": camera.get("ip_address"),
        "protocol": protocol,
        "driver": "hikvision_isapi" if driver == "isapi" else "onvif_media",
    }

    if driver == "onvif":
        return await _try_onvif_stream_profile(camera, base)

    main_ch = _channel_id(camera, "main")
    sub_ch = _channel_id(camera, "sub")
    try:
        main_xml, main_cap, main_err = await _read_channel(camera, main_ch)
        sub_xml, sub_cap, sub_err = await _read_channel(camera, sub_ch)
    except Exception as exc:
        logger.warning("[stream-profile] read failed for %s: %s", camera_id, exc)
        base["supported"] = False
        base["message"] = str(exc)
        base["main"] = _unsupported_block("main", main_ch, str(exc))
        base["sub"] = _unsupported_block("sub", sub_ch, str(exc))
        return base

    base["supported"] = True
    base["message"] = None
    base["main"] = _build_profile_block("main", main_ch, main_xml or "", main_cap, read_error=main_err)
    base["sub"] = _build_profile_block("sub", sub_ch, sub_xml or "", sub_cap, read_error=sub_err)
    return base


def _unsupported_block(profile: str, channel: str, message: str = "Not supported") -> Dict[str, Any]:
    label = _stream_label(profile) if channel else ("Main" if profile == "main" else "Sub")
    return {
        "profile": profile,
        "channel": channel,
        "label": label,
        "supported": False,
        "message": message,
        "current": None,
        "capabilities": None,
    }


async def apply_camera_stream_profile(camera_id: str, payload: dict) -> Dict[str, Any]:
    """Apply FPS/resolution to one camera only (ISAPI PUT). Does not touch Mongo or other cameras."""
    try:
        oid = ObjectId(camera_id)
    except (InvalidId, TypeError):
        return {"ok": False, "error": "Invalid camera id"}

    camera = await camera_collection.find_one({"_id": oid})
    if not camera:
        return {"ok": False, "error": "Camera not found"}

    protocol = normalize_make(camera.get("protocol") or "")
    driver = resolve_stream_driver(protocol)
    if driver == "onvif":
        return await _try_onvif_apply(camera, camera_id, payload)

    results: Dict[str, Any] = {"ok": True, "cameraId": camera_id, "applied": {}}
    for profile in ("main", "sub"):
        block = payload.get(profile)
        if not block or not isinstance(block, dict):
            continue
        channel = _channel_id(camera, profile)
        try:
            results["applied"][profile] = await _apply_channel_profile(camera, channel, block)
        except Exception as exc:
            logger.warning("[stream-profile] apply %s ch%s failed: %s", camera_id, channel, exc)
            results["applied"][profile] = {"ok": False, "error": str(exc)}
            results["ok"] = False

    return results


async def _apply_channel_profile(camera: dict, channel: str, block: dict) -> Dict[str, Any]:
    path = f"/ISAPI/Streaming/channels/{channel}"
    status, xml = await _isapi(camera, "GET", path)
    if status != 200 or not xml:
        return {"ok": False, "error": f"ISAPI read failed (HTTP {status})"}

    before = parse_encoder_fields(xml)
    updated = xml
    changes: Dict[str, Any] = {}

    if "fps" in block and block["fps"] is not None:
        if not re.search(r"<maxFrameRate>", xml):
            return {"ok": False, "error": "FPS not supported on this stream"}
        try:
            fps = int(block["fps"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid fps"}
        if fps < MIN_FPS or fps > MAX_FPS:
            return {"ok": False, "error": f"FPS must be between {MIN_FPS} and {MAX_FPS}"}
        api_val = hik_fps_to_api(fps)
        updated = replace_tag(updated, "maxFrameRate", api_val)
        changes["fps"] = fps
        # Keep ~1s GOP when camera exposes GOP tags.
        if re.search(r"<GovLength>", updated):
            updated = replace_tag(updated, "GovLength", str(max(1, fps)))
        if re.search(r"<keyFrameInterval>", updated):
            updated = replace_tag(updated, "keyFrameInterval", "1000")

    width = block.get("width")
    height = block.get("height")
    if width is not None and height is not None:
        w, h = str(int(width)), str(int(height))
        if not re.search(r"<videoResolutionWidth>", updated):
            return {"ok": False, "error": "Resolution not supported on this stream"}
        updated = replace_tag(updated, "videoResolutionWidth", w)
        updated = replace_tag(updated, "videoResolutionHeight", h)
        changes["width"] = int(w)
        changes["height"] = int(h)

    if not changes:
        return {"ok": False, "error": "No changes requested"}

    put_status, put_text = await _isapi(camera, "PUT", path, body=updated.encode("utf-8"))
    if put_status not in (200, 201):
        return {"ok": False, "error": f"ISAPI PUT failed (HTTP {put_status})", "detail": (put_text or "")[:200]}

    _, after_xml = await _isapi(camera, "GET", path)
    after = parse_encoder_fields(after_xml or "")
    return {
        "ok": True,
        "channel": channel,
        "before": {
            "fps": hik_fps_from_api(before.get("maxFrameRate")),
            "width": before.get("videoResolutionWidth"),
            "height": before.get("videoResolutionHeight"),
        },
        "after": {
            "fps": hik_fps_from_api(after.get("maxFrameRate")),
            "width": after.get("videoResolutionWidth"),
            "height": after.get("videoResolutionHeight"),
        },
        "changes": changes,
    }
