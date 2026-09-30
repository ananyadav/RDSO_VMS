"""Motion event capability / ISAPI motion detection probes (RDSO 18.3.14)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Optional

from app.services.hikvision_ptz import _isapi
from app.services.ptz_control import _brand, _protocol
from app.services.rtsp_utils import build_camera_rtsp_urls, normalize_make

logger = logging.getLogger(__name__)

HIK_FAMILY = frozenset({"HIKVISION", "HIK", "PRAMA", "HONEYWELL", "SPARSH"})


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _prefer_isapi(camera: dict) -> bool:
    proto = normalize_make(_protocol(camera) or "")
    brand = normalize_make(_brand(camera) or "")
    return proto in HIK_FAMILY or brand in HIK_FAMILY or "HIK" in brand


def camera_has_dual_streams(camera: dict) -> bool:
    main = (camera.get("main_rtsp_url") or "").strip()
    sub = (camera.get("sub_rtsp_url") or "").strip()
    if main and sub and main != sub:
        return True
    urls = build_camera_rtsp_urls(camera)
    main = (urls.get("main_rtsp_url") or "").strip()
    sub = (urls.get("sub_rtsp_url") or "").strip()
    return bool(main and sub and main != sub)


def parse_motion_detection_xml(text: str) -> dict[str, Any]:
    """Parse ISAPI motionDetection config/status XML."""
    out: dict[str, Any] = {
        "enabled": None,
        "raw_ok": bool(text and text.strip()),
    }
    if not text:
        return out
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        lower = text.lower()
        out["enabled"] = "enabled>true" in lower or "<enabled>true</enabled>" in lower
        return out
    for node in root.iter():
        tag = _local(node.tag).lower()
        val = (node.text or "").strip().lower()
        if tag == "enabled" and val:
            out["enabled"] = val in ("true", "1", "yes")
        if tag in ("sensitivitylevel", "sensitivity") and val:
            out["sensitivity"] = val
    return out


async def probe_isapi_motion(camera: dict) -> dict[str, Any]:
    """Probe whether ISAPI motionDetection endpoint exists (config, not fake MD)."""
    result: dict[str, Any] = {
        "protocol": "hikvision_isapi",
        "supported": False,
        "motion_config_present": False,
        "message": "",
        "details": {},
    }
    channel = 1
    try:
        ch = camera.get("main_channel") or camera.get("ptz_channel") or 1
        channel = max(1, int(str(ch)[0]) if str(ch) else 1)
        # main_channel often 101 → channel 1
        raw = str(camera.get("main_channel") or "101")
        if raw.isdigit() and len(raw) >= 3:
            channel = max(1, int(raw[0]))
    except (TypeError, ValueError):
        channel = 1

    paths = (
        f"/ISAPI/System/Video/inputs/channels/{channel}/motionDetection",
        "/ISAPI/System/Video/inputs/channels/1/motionDetection",
        "/ISAPI/Smart/motionDetection/1",
    )
    last_status = 0
    last_text = ""
    for path in paths:
        try:
            status, text = await _isapi(camera, "GET", path)
        except Exception as exc:
            result["details"]["error"] = str(exc)
            continue
        last_status, last_text = status, text or ""
        result["details"]["path"] = path
        result["details"]["http_status"] = status
        if status == 200 and text:
            parsed = parse_motion_detection_xml(text)
            result["motion_config_present"] = True
            result["supported"] = True
            result["details"]["parsed"] = parsed
            result["message"] = "ISAPI motionDetection available"
            return result
        if status in (401, 403):
            result["message"] = "ISAPI authentication failed for motionDetection"
            return result
    if last_status == 404:
        result["message"] = "Camera does not expose ISAPI motionDetection"
    else:
        result["message"] = (
            f"ISAPI motionDetection not usable (HTTP {last_status or 'n/a'}); "
            "motion/activity recording not claimed for auto-poll"
        )
    return result


async def detect_motion_capability(camera: dict) -> dict[str, Any]:
    """Whether this camera can participate in RDSO 18.3.14 dual-stream activity mode."""
    camera_id = str(camera.get("_id") or camera.get("id") or "")
    dual = camera_has_dual_streams(camera)
    base: dict[str, Any] = {
        "camera_id": camera_id,
        "supported": False,
        "dual_stream": dual,
        "motion_events": False,
        "protocol": "unsupported",
        "message": "",
        "probes": {},
    }
    if not dual:
        base["message"] = (
            "Unsupported: camera needs distinct main and sub RTSP streams "
            "for active/idle recording modes"
        )
        return base

    override = (camera.get("motion_recording") or {}).get("motion_source") if isinstance(
        camera.get("motion_recording"), dict
    ) else None
    override = str(override or "auto").strip().lower()

    if override == "disabled":
        base["message"] = "Motion/activity recording disabled for this camera"
        return base

    if override == "signal":
        base["supported"] = True
        base["motion_events"] = True
        base["protocol"] = "signal"
        base["message"] = (
            "Supported via injected motion signals (ISAPI auto-poll not required)"
        )
        return base

    if override in ("auto", "isapi") and _prefer_isapi(camera):
        probe = await probe_isapi_motion(camera)
        base["probes"]["isapi"] = probe
        if probe.get("supported"):
            base["supported"] = True
            base["motion_events"] = True
            base["protocol"] = "hikvision_isapi"
            base["message"] = probe.get("message") or "ISAPI motion supported"
            return base
        if override == "isapi":
            base["message"] = probe.get("message") or "ISAPI motion not available"
            return base

    # Non-Hik or ISAPI failed: allow signal-driven mode if dual-stream exists
    if override == "auto":
        base["supported"] = True
        base["motion_events"] = True
        base["protocol"] = "signal"
        base["message"] = (
            "Supported for signal-driven activity mode (dual streams present). "
            "ISAPI motion auto-poll not available on this device — use motion "
            "alarm rules / POST motion-activity ingest."
        )
        return base

    base["message"] = "Motion/activity recording not supported on this camera"
    return base
