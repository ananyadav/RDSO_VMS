"""Hikvision ISAPI PTZ control (continuous move, zoom, presets)."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from app.services.http_digest import request_with_digest

logger = logging.getLogger(__name__)

ISAPI_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
DEFAULT_HTTP_PORT = 80
DEFAULT_PTZ_CHANNEL = 1

SPEED_MAP = {1: 35, 2: 60, 3: 90}


def _ptz_xml(pan: int, tilt: int, zoom: int) -> bytes:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<PTZData>"
        f"<pan>{int(pan)}</pan>"
        f"<tilt>{int(tilt)}</tilt>"
        f"<zoom>{int(zoom)}</zoom>"
        "</PTZData>"
    )
    return body.encode("utf-8")


def _preset_set_xml(preset_id: int, name: str) -> bytes:
    safe_name = (name or f"Preset {preset_id}").strip()[:64]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<PTZPreset>"
        "<enabled>true</enabled>"
        f"<id>{int(preset_id)}</id>"
        f"<presetName>{_escape_xml(safe_name)}</presetName>"
        "</PTZPreset>"
    )
    return body.encode("utf-8")


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _camera_http_port(camera: dict) -> int:
    for key in ("http_port", "isapi_port"):
        val = camera.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return DEFAULT_HTTP_PORT


def _ptz_channel(camera: dict) -> int:
    val = camera.get("ptz_channel")
    if val is not None:
        try:
            return max(1, int(val))
        except (TypeError, ValueError):
            pass
    return DEFAULT_PTZ_CHANNEL


def _isapi_base(camera: dict) -> str:
    ip = (camera.get("ip_address") or camera.get("ip") or "").strip()
    if not ip:
        raise ValueError("Camera has no IP address")
    port = _camera_http_port(camera)
    if port in (443, 8443):
        return f"https://{ip}:{port}"
    return f"http://{ip}:{port}"


def _credentials(camera: dict) -> Tuple[str, str]:
    username = (camera.get("username") or "admin").strip()
    password = str(camera.get("password") or "")
    if not password:
        raise ValueError("Camera password not configured")
    return username, password


def _headers() -> Dict[str, str]:
    return {"Content-Type": "application/xml; charset=UTF-8"}


async def _isapi(
    camera: dict,
    method: str,
    path: str,
    *,
    body: Optional[bytes] = None,
) -> Tuple[int, str]:
    base = _isapi_base(camera)
    url = f"{base}{path}"
    username, password = _credentials(camera)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        status, text = await request_with_digest(
            session,
            method,
            url,
            username=username,
            password=password,
            data=body,
            headers=_headers() if body is not None else None,
            timeout=ISAPI_TIMEOUT,
        )
    return status, text


def _parse_presets_xml(text: str) -> List[Dict[str, Any]]:
    presets: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return presets

    for node in root.iter():
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
        if tag != "PTZPreset":
            continue
        preset_id = None
        name = None
        enabled = True
        for child in node:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            val = (child.text or "").strip()
            if child_tag == "id" and val.isdigit():
                preset_id = int(val)
            elif child_tag == "presetName":
                name = val
            elif child_tag == "enabled":
                enabled = val.lower() in ("true", "1", "yes")
        if preset_id is not None:
            presets.append(
                {
                    "id": preset_id,
                    "name": name or f"Preset {preset_id}",
                    "enabled": enabled,
                }
            )
    presets.sort(key=lambda p: p["id"])
    return presets


def _direction_to_velocity(direction: str, speed: int) -> Tuple[int, int, int]:
    spd = SPEED_MAP.get(max(1, min(3, speed)), 60)
    d = (direction or "").lower().strip()
    if d == "up":
        return 0, spd, 0
    if d == "down":
        return 0, -spd, 0
    if d == "left":
        return -spd, 0, 0
    if d == "right":
        return spd, 0, 0
    if d == "zoom_in":
        return 0, 0, spd
    if d == "zoom_out":
        return 0, 0, -spd
    if d == "home":
        return 0, 0, 0
    return 0, 0, 0


async def ptz_continuous(
    camera: dict,
    *,
    pan: int = 0,
    tilt: int = 0,
    zoom: int = 0,
) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/continuous"
    status, text = await _isapi(camera, "PUT", path, body=_ptz_xml(pan, tilt, zoom))
    if status not in (200, 201, 204):
        logger.warning("[PTZ] continuous failed status=%s body=%s", status, text[:300])
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "status": status}


async def ptz_stop(camera: dict) -> Dict[str, Any]:
    return await ptz_continuous(camera, pan=0, tilt=0, zoom=0)


async def ptz_move_direction(camera: dict, direction: str, *, speed: int = 2) -> Dict[str, Any]:
    pan, tilt, zoom = _direction_to_velocity(direction, speed)
    return await ptz_continuous(camera, pan=pan, tilt=tilt, zoom=zoom)


async def list_presets(camera: dict) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    status, text = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/presets")
    if status != 200:
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "presets": []}
    presets = _parse_presets_xml(text)
    return {"ok": True, "presets": presets}


async def goto_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/presets/{int(preset_id)}/goto"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True}


async def set_preset(camera: dict, preset_id: int, name: str) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/presets/{int(preset_id)}"
    status, text = await _isapi(
        camera,
        "PUT",
        path,
        body=_preset_set_xml(preset_id, name),
    )
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True}


async def delete_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/presets/{int(preset_id)}"
    status, text = await _isapi(camera, "DELETE", path)
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True}


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    """Quick check whether ISAPI PTZ endpoints respond."""
    channel = _ptz_channel(camera)
    status, text = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/capabilities")
    if status == 200:
        return {"ok": True, "supported": True}
    status2, text2 = await _isapi(camera, "GET", "/ISAPI/PTZCtrl/channels")
    if status2 == 200 and "PTZChannel" in text2:
        return {"ok": True, "supported": True}
    return {
        "ok": False,
        "supported": False,
        "error": _error_from_response(status2, text2),
    }


def _error_from_response(status: int, text: str) -> str:
    if status in (401, 403):
        return "Camera rejected credentials (check username/password)"
    if status == 404:
        return "PTZ not supported on this camera or wrong channel"
    if status == 0 or not text:
        return "Camera unreachable on HTTP (check IP and port 80)"
    snippet = text.strip().replace("\n", " ")[:180]
    return f"Camera returned HTTP {status}: {snippet}"
