"""ONVIF PTZ (SOAP) for mixed-brand cameras stored as ONVIF/CUSTOM."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import aiohttp

from app.services.http_digest import request_with_digest

logger = logging.getLogger(__name__)

ONVIF_TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
SPEED_MAP = {1: 0.35, 2: 0.6, 3: 1.0}
_PROFILE_CACHE: Dict[str, str] = {}
_SERVICE_CACHE: Dict[str, Dict[str, str]] = {}
_AUTH_CACHE: Dict[str, str] = {}

_SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_TRT = "http://www.onvif.org/ver10/media/wsdl"
_TPTZ = "http://www.onvif.org/ver20/ptz/wsdl"
_TT = "http://www.onvif.org/ver10/schema"
_TDS = "http://www.onvif.org/ver10/device/wsdl"
_PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)
_PASSWORD_TEXT = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText"
)

# Uniview/OEM cameras that use /media/video1 often expose ONVIF on device_service.
_DEVICE_PATHS = (
    "/onvif/device_service",
    "/onvif/device",
    "/onvif/Device",
    "/device_service",
)
_MEDIA_PATHS = (
    "/onvif/media_service",
    "/onvif/Media",
    "/onvif/media",
    "/Media",
)
_PTZ_PATHS = (
    "/onvif/ptz_service",
    "/onvif/PTZ",
    "/onvif/ptz",
    "/PTZ",
)


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _camera_key(camera: dict) -> str:
    return str(camera.get("_id") or camera.get("id") or camera.get("ip_address") or "")


def _http_port(camera: dict) -> int:
    for key in ("http_port", "onvif_port", "isapi_port"):
        val = camera.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 80


def _base_url(camera: dict) -> str:
    ip = (camera.get("ip_address") or camera.get("ip") or "").strip()
    if not ip:
        raise ValueError("Camera has no IP address")
    port = _http_port(camera)
    if port in (443, 8443):
        return f"https://{ip}:{port}"
    return f"http://{ip}:{port}"


def _credentials(camera: dict) -> Tuple[str, str]:
    username = (camera.get("username") or "admin").strip()
    password = str(camera.get("password") or "")
    if not password:
        raise ValueError("Camera password not configured")
    return username, password


def _wsse_header(username: str, password: str, *, password_text: bool = False) -> str:
    nonce_raw = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce_raw).decode("ascii")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if password_text:
        password_el = (
            f'<wsse:Password Type="{_PASSWORD_TEXT}">{_xml_escape(password)}</wsse:Password>'
        )
    else:
        digest = base64.b64encode(
            hashlib.sha1(nonce_raw + created.encode("utf-8") + password.encode("utf-8")).digest()
        ).decode("ascii")
        password_el = f'<wsse:Password Type="{_PASSWORD_DIGEST}">{digest}</wsse:Password>'
    return (
        f'<wsse:Security s:mustUnderstand="1" xmlns:wsse="{_WSSE}" xmlns:wsu="{_WSU}">'
        f"<wsse:UsernameToken>"
        f"<wsse:Username>{_xml_escape(username)}</wsse:Username>"
        f"{password_el}"
        f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>'
        f"<wsu:Created>{created}</wsu:Created>"
        f"</wsse:UsernameToken>"
        f"</wsse:Security>"
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _envelope(username: str, password: str, body: str, *, password_text: bool = False) -> bytes:
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{_SOAP_NS}">'
        f"<s:Header>{_wsse_header(username, password, password_text=password_text)}</s:Header>"
        f"<s:Body>{body}</s:Body>"
        f"</s:Envelope>"
    )
    return xml.encode("utf-8")


def _rewrite_xaddr(camera: dict, xaddr: str) -> str:
    """Keep the camera's reachable IP/port; use the path from GetCapabilities."""
    parsed = urlparse((xaddr or "").strip())
    if not parsed.path:
        return ""
    ip = (camera.get("ip_address") or camera.get("ip") or "").strip()
    if not ip:
        return xaddr.strip()
    scheme = parsed.scheme or "http"
    port = parsed.port or _http_port(camera)
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = ip
    else:
        netloc = f"{ip}:{port}"
    return urlunparse((scheme, netloc, parsed.path, "", parsed.query, ""))


def _xaddrs_from_capabilities(text: str) -> Dict[str, str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    out: Dict[str, str] = {}
    current: Optional[str] = None
    for node in root.iter():
        name = _local(node.tag)
        if name in ("Media", "PTZ", "Device"):
            current = name.lower()
            attr = (node.attrib.get("XAddr") or "").strip()
            if attr:
                out[current] = attr
        elif name == "XAddr" and current and (node.text or "").strip():
            out[current] = (node.text or "").strip()
            current = None
    return out


def _direction_velocity(direction: str, speed: int) -> Tuple[float, float, float]:
    spd = SPEED_MAP.get(max(1, min(3, speed)), 0.6)
    d = (direction or "").lower().strip()
    if d == "up":
        return 0.0, spd, 0.0
    if d == "down":
        return 0.0, -spd, 0.0
    if d == "left":
        return -spd, 0.0, 0.0
    if d == "right":
        return spd, 0.0, 0.0
    if d == "zoom_in":
        return 0.0, 0.0, spd
    if d == "zoom_out":
        return 0.0, 0.0, -spd
    return 0.0, 0.0, 0.0


async def _soap(
    camera: dict,
    url: str,
    action: str,
    body: str,
    *,
    password_text: bool = False,
) -> Tuple[int, str]:
    username, password = _credentials(camera)
    payload = _envelope(username, password, body, password_text=password_text)
    headers = {
        "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
        "SOAPAction": f'"{action}"',
    }
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        status, text = await request_with_digest(
            session,
            "POST",
            url,
            username=username,
            password=password,
            data=payload,
            headers=headers,
            timeout=ONVIF_TIMEOUT,
        )
    return status, text


def _auth_modes(camera: dict) -> Tuple[bool, ...]:
    cached = _AUTH_CACHE.get(_camera_key(camera))
    if cached == "text":
        return (True,)
    if cached == "digest":
        return (False,)
    return (False, True)


async def _soap_with_auth(camera: dict, url: str, action: str, body: str) -> Tuple[int, str]:
    last_status, last_text = 0, ""
    for password_text in _auth_modes(camera):
        try:
            status, text = await _soap(camera, url, action, body, password_text=password_text)
        except Exception as exc:
            last_status, last_text = 0, str(exc)
            continue
        last_status, last_text = status, text
        if status in (200, 201, 204) and "Fault" not in text:
            _AUTH_CACHE[_camera_key(camera)] = "text" if password_text else "digest"
            return status, text
        if status not in (401, 403):
            return status, text
    return last_status, last_text


def _first_text(root: ET.Element, local_name: str) -> Optional[str]:
    for node in root.iter():
        if _local(node.tag) == local_name and (node.text or "").strip():
            return (node.text or "").strip()
    return None


async def _try_urls(camera: dict, urls: List[str], action: str, body: str) -> Tuple[int, str, str]:
    last_status, last_text, last_url = 0, "", ""
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            status, text = await _soap_with_auth(camera, url, action, body)
        except Exception as exc:
            last_status, last_text, last_url = 0, str(exc), url
            continue
        last_status, last_text, last_url = status, text, url
        if status in (200, 201, 204) and "Fault" not in text:
            return status, text, url
    return last_status, last_text, last_url


async def _try_paths(camera: dict, paths: tuple[str, ...], action: str, body: str) -> Tuple[int, str, str]:
    base = _base_url(camera)
    return await _try_urls(camera, [f"{base}{path}" for path in paths], action, body)


async def _discover_services(camera: dict) -> Dict[str, str]:
    key = _camera_key(camera)
    cached = _SERVICE_CACHE.get(key)
    if cached is not None:
        return cached
    body = (
        f'<tds:GetCapabilities xmlns:tds="{_TDS}">'
        f"<tds:Category>All</tds:Category>"
        f"</tds:GetCapabilities>"
    )
    status, text, _url = await _try_paths(
        camera,
        _DEVICE_PATHS,
        f"{_TDS}/GetCapabilities",
        body,
    )
    services: Dict[str, str] = {}
    if status in (200, 201) and "Fault" not in text:
        for name, xaddr in _xaddrs_from_capabilities(text).items():
            rewritten = _rewrite_xaddr(camera, xaddr)
            if rewritten:
                services[name] = rewritten
    _SERVICE_CACHE[key] = services
    return services


def _service_urls(camera: dict, discovered: Optional[str], fallbacks: tuple[str, ...]) -> List[str]:
    base = _base_url(camera)
    urls: List[str] = []
    if discovered:
        urls.append(discovered)
    urls.extend(f"{base}{path}" for path in fallbacks)
    return urls


async def _try_ptz(camera: dict, action: str, body: str) -> Tuple[int, str, str]:
    # Use cached XAddrs only. GetCapabilities while RTSP is live drops OEM PTZs.
    services = _SERVICE_CACHE.get(_camera_key(camera)) or {}
    return await _try_urls(
        camera,
        _service_urls(camera, services.get("ptz"), _PTZ_PATHS),
        action,
        body,
    )


async def _get_profile_token(camera: dict) -> Tuple[Optional[str], Dict[str, Any]]:
    key = _camera_key(camera)
    cached = _PROFILE_CACHE.get(key)
    if cached:
        return cached, {"ok": True}

    # Skip GetCapabilities — Uniview/OEM cameras drop the live RTSP producer.
    body = f'<trt:GetProfiles xmlns:trt="{_TRT}"/>'
    status, text, _url = await _try_urls(
        camera,
        _service_urls(camera, None, _MEDIA_PATHS),
        f"{_TRT}/GetProfiles",
        body,
    )
    if status not in (200, 201) or "Fault" in text:
        return None, {
            "ok": False,
            "status": status,
            "error": _error_from_response(status, text),
        }
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, {"ok": False, "status": status, "error": "Invalid ONVIF GetProfiles response"}
    token = None
    for node in root.iter():
        if _local(node.tag) == "Profiles":
            token = node.attrib.get("token")
            if token:
                break
    if not token:
        token = _first_text(root, "token")
    if not token:
        return None, {"ok": False, "status": status, "error": "ONVIF camera returned no media profile"}
    _PROFILE_CACHE[key] = token
    return token, {"ok": True}


def _error_from_response(status: int, text: str) -> str:
    if status in (401, 403):
        return "Camera rejected credentials (check username/password)"
    if status == 0 or not text:
        port = "HTTP"
        return f"ONVIF PTZ not responding ({port}) — check camera IP/credentials or http_port"
    snippet = text.strip().replace("\n", " ")[:180]
    if "Fault" in text:
        return f"ONVIF fault: {snippet}"
    return f"Camera returned HTTP {status}: {snippet}"


async def ptz_continuous(
    camera: dict,
    *,
    pan: float = 0.0,
    tilt: float = 0.0,
    zoom: float = 0.0,
) -> Dict[str, Any]:
    token, err = await _get_profile_token(camera)
    if not token:
        return err
    body = (
        f'<tptz:ContinuousMove xmlns:tptz="{_TPTZ}" xmlns:tt="{_TT}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:Velocity>"
        f'<tt:PanTilt x="{pan:.3f}" y="{tilt:.3f}"/>'
        f'<tt:Zoom x="{zoom:.3f}"/>'
        f"</tptz:Velocity>"
        f"</tptz:ContinuousMove>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/ContinuousMove", body)
    if status not in (200, 201, 204) or "Fault" in text:
        logger.warning("[PTZ-ONVIF] ContinuousMove failed status=%s body=%s", status, text[:300])
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "status": status, "backend": "onvif"}


async def ptz_stop(camera: dict) -> Dict[str, Any]:
    token, err = await _get_profile_token(camera)
    if not token:
        return err
    body = (
        f'<tptz:Stop xmlns:tptz="{_TPTZ}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:PanTilt>true</tptz:PanTilt>"
        f"<tptz:Zoom>true</tptz:Zoom>"
        f"</tptz:Stop>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/Stop", body)
    if status not in (200, 201, 204) or "Fault" in text:
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "onvif"}


async def ptz_move_direction(camera: dict, direction: str, *, speed: int = 2) -> Dict[str, Any]:
    pan, tilt, zoom = _direction_velocity(direction, speed)
    if pan == 0 and tilt == 0 and zoom == 0:
        return await ptz_stop(camera)
    return await ptz_continuous(camera, pan=pan, tilt=tilt, zoom=zoom)


async def list_presets(camera: dict) -> Dict[str, Any]:
    token, err = await _get_profile_token(camera)
    if not token:
        err["presets"] = []
        return err
    body = (
        f'<tptz:GetPresets xmlns:tptz="{_TPTZ}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"</tptz:GetPresets>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/GetPresets", body)
    if status != 200 or "Fault" in text:
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "presets": []}
    presets: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"ok": False, "error": "Invalid ONVIF GetPresets response", "presets": []}
    for node in root.iter():
        if _local(node.tag) != "Preset":
            continue
        preset_token = node.attrib.get("token") or ""
        name = ""
        for child in node:
            if _local(child.tag) == "Name":
                name = (child.text or "").strip()
        if not preset_token:
            continue
        try:
            preset_id = int(preset_token)
        except ValueError:
            preset_id = abs(hash(preset_token)) % 10000
        presets.append({"id": preset_id, "name": name or f"Preset {preset_token}", "token": preset_token})
    presets.sort(key=lambda p: p["id"])
    return {"ok": True, "presets": presets, "backend": "onvif"}


async def goto_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    listed = await list_presets(camera)
    if not listed.get("ok"):
        return listed
    match = next((p for p in listed.get("presets") or [] if p.get("id") == int(preset_id)), None)
    token, err = await _get_profile_token(camera)
    if not token:
        return err
    preset_token = str((match or {}).get("token") or preset_id)
    body = (
        f'<tptz:GotoPreset xmlns:tptz="{_TPTZ}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{_xml_escape(preset_token)}</tptz:PresetToken>"
        f"</tptz:GotoPreset>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/GotoPreset", body)
    if status not in (200, 201, 204) or "Fault" in text:
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "onvif"}


async def set_preset(camera: dict, preset_id: int, name: str) -> Dict[str, Any]:
    token, err = await _get_profile_token(camera)
    if not token:
        return err
    body = (
        f'<tptz:SetPreset xmlns:tptz="{_TPTZ}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:PresetName>{_xml_escape(name or f'Preset {preset_id}')}</tptz:PresetName>"
        f"</tptz:SetPreset>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/SetPreset", body)
    if status not in (200, 201, 204) or "Fault" in text:
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "onvif"}


async def delete_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    listed = await list_presets(camera)
    if not listed.get("ok"):
        return listed
    match = next((p for p in listed.get("presets") or [] if p.get("id") == int(preset_id)), None)
    token, err = await _get_profile_token(camera)
    if not token:
        return err
    preset_token = str((match or {}).get("token") or preset_id)
    body = (
        f'<tptz:RemovePreset xmlns:tptz="{_TPTZ}">'
        f"<tptz:ProfileToken>{_xml_escape(token)}</tptz:ProfileToken>"
        f"<tptz:PresetToken>{_xml_escape(preset_token)}</tptz:PresetToken>"
        f"</tptz:RemovePreset>"
    )
    status, text, _url = await _try_ptz(camera, f"{_TPTZ}/RemovePreset", body)
    if status not in (200, 201, 204) or "Fault" in text:
        return {"ok": False, "status": status, "error": _error_from_response(status, text)}
    return {"ok": True, "backend": "onvif"}


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    token, err = await _get_profile_token(camera)
    if token:
        return {"ok": True, "supported": True, "backend": "onvif"}
    return {"ok": False, "supported": False, "error": err.get("error") or "ONVIF PTZ not available"}
