"""Dahua HTTP CGI PTZ."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple
from urllib.parse import quote

import aiohttp

from app.services.http_digest import request_with_digest

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
SPEED_MAP = {1: 2, 2: 4, 3: 8}
CODES = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "zoom_in": "ZoomTele",
    "zoom_out": "ZoomWide",
}


def _http_port(camera: dict) -> int:
    for key in ("http_port", "isapi_port"):
        val = camera.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 80


def _base(camera: dict) -> str:
    ip = (camera.get("ip_address") or camera.get("ip") or "").strip()
    if not ip:
        raise ValueError("Camera has no IP address")
    port = _http_port(camera)
    scheme = "https" if port in (443, 8443) else "http"
    return f"{scheme}://{ip}:{port}"


def _credentials(camera: dict) -> Tuple[str, str]:
    username = (camera.get("username") or "admin").strip()
    password = str(camera.get("password") or "")
    if not password:
        raise ValueError("Camera password not configured")
    return username, password


def _error_from_response(status: int, text: str) -> str:
    if status in (401, 403):
        return "Camera rejected credentials (check username/password)"
    if status == 0 or not text:
        return "Dahua PTZ not responding on HTTP — check camera IP/credentials or http_port"
    snippet = text.strip().replace("\n", " ")[:180]
    return f"Camera returned HTTP {status}: {snippet}"


async def _cgi(camera: dict, query: str) -> Tuple[int, str]:
    url = f"{_base(camera)}/cgi-bin/ptz.cgi?{query}"
    username, password = _credentials(camera)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        status, text = await request_with_digest(
            session,
            "GET",
            url,
            username=username,
            password=password,
            timeout=TIMEOUT,
        )
    return status, text


async def ptz_move_direction(camera: dict, direction: str, *, speed: int = 2) -> Dict[str, Any]:
    code = CODES.get((direction or "").lower().strip())
    if not code:
        return await ptz_stop(camera)
    spd = SPEED_MAP.get(max(1, min(3, speed)), 4)
    status, text = await _cgi(
        camera,
        f"action=start&channel=0&code={quote(code)}&arg1=0&arg2={spd}&arg3=0",
    )
    if status not in (200, 204) or (text and "error" in text.lower() and "ok" not in text.lower()):
        logger.warning("[PTZ-DAHUA] start failed status=%s body=%s", status, text[:200])
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "status": status, "backend": "dahua"}


async def ptz_continuous(camera: dict, *, pan: int = 0, tilt: int = 0, zoom: int = 0) -> Dict[str, Any]:
    if pan > 0:
        return await ptz_move_direction(camera, "right")
    if pan < 0:
        return await ptz_move_direction(camera, "left")
    if tilt > 0:
        return await ptz_move_direction(camera, "up")
    if tilt < 0:
        return await ptz_move_direction(camera, "down")
    if zoom > 0:
        return await ptz_move_direction(camera, "zoom_in")
    if zoom < 0:
        return await ptz_move_direction(camera, "zoom_out")
    return await ptz_stop(camera)


async def ptz_stop(camera: dict) -> Dict[str, Any]:
    last_error = {"ok": False, "error": "Dahua PTZ stop failed"}
    for code in ("Up", "Down", "Left", "Right", "ZoomTele", "ZoomWide"):
        status, text = await _cgi(
            camera,
            f"action=stop&channel=0&code={code}&arg1=0&arg2=0&arg3=0",
        )
        if status in (200, 204):
            return {"ok": True, "backend": "dahua"}
        last_error = {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return last_error


async def list_presets(camera: dict) -> Dict[str, Any]:
    return {"ok": True, "presets": [], "backend": "dahua"}


async def goto_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    status, text = await _cgi(
        camera,
        f"action=start&channel=0&code=GotoPreset&arg1=0&arg2={int(preset_id)}&arg3=0",
    )
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "dahua"}


async def set_preset(camera: dict, preset_id: int, name: str) -> Dict[str, Any]:
    status, text = await _cgi(
        camera,
        f"action=start&channel=0&code=SetPreset&arg1=0&arg2={int(preset_id)}&arg3=0",
    )
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "dahua", "name": name}


async def delete_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    status, text = await _cgi(
        camera,
        f"action=start&channel=0&code=ClearPreset&arg1=0&arg2={int(preset_id)}&arg3=0",
    )
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "dahua"}


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    status, text = await _cgi(camera, "action=getStatus")
    if status in (200, 204):
        return {"ok": True, "supported": True, "backend": "dahua"}
    return {"ok": False, "supported": False, "error": _error_from_response(status, text)}
