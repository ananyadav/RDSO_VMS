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


def _parse_patrols_xml(text: str) -> List[Dict[str, Any]]:
    tours: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return tours

    for node in root.iter():
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
        if tag != "PTZPatrol":
            continue
        tour_id: Optional[int] = None
        name = ""
        enabled = True
        steps: List[Dict[str, Any]] = []
        for child in node:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            val = (child.text or "").strip()
            if child_tag == "id" and val.isdigit():
                tour_id = int(val)
            elif child_tag in ("patrolName", "name"):
                name = val
            elif child_tag == "enabled":
                enabled = val.lower() in ("true", "1", "yes")
            elif child_tag in ("PatrolSequenceList", "PatrolList"):
                for seq in child:
                    seq_tag = seq.tag.split("}")[-1] if "}" in seq.tag else seq.tag
                    if seq_tag not in ("PatrolSequence", "Patrol"):
                        continue
                    step: Dict[str, Any] = {"presetId": None, "delay": 15, "speed": 30}
                    for field in seq:
                        ft = field.tag.split("}")[-1] if "}" in field.tag else field.tag
                        fv = (field.text or "").strip()
                        if ft in ("presetID", "presetId") and fv.isdigit():
                            step["presetId"] = int(fv)
                        elif ft in ("delay", "dwellTime") and fv.isdigit():
                            step["delay"] = int(fv)
                        elif ft in ("speed", "seqSpeed") and fv.isdigit():
                            step["speed"] = int(fv)
                        elif ft == "seq" and fv.isdigit():
                            step["seq"] = int(fv)
                    # presetID 0 = empty patrol slot on Hikvision
                    if step["presetId"] is not None and step["presetId"] > 0:
                        steps.append(step)
        if tour_id is not None:
            tours.append(
                {
                    "id": tour_id,
                    "name": name or f"Tour {tour_id}",
                    "enabled": enabled,
                    "steps": steps,
                }
            )
    tours.sort(key=lambda t: t["id"])
    return tours


def _patrol_set_xml(tour_id: int, name: str, steps: List[Dict[str, Any]], *, enabled: bool = True) -> bytes:
    """Build Hikvision PTZPatrol XML.

    Real devices (ISAPI 2.0) expect PatrolSequence with presetID/seqSpeed/delay,
    often a fixed-length list padded with presetID=0 empty slots.
    """
    safe_name = (name or f"Tour {tour_id}").strip()[:64]
    sequences: List[str] = []
    for step in steps:
        preset_id = int(step.get("presetId") or step.get("preset_id") or 0)
        if preset_id <= 0:
            continue
        # Many Hikvision domes reject delay < 15.
        delay = max(15, int(step.get("delay") or 15))
        speed = int(step.get("speed") or 30)
        sequences.append(
            "<PatrolSequence>"
            f"<presetID>{preset_id}</presetID>"
            f"<seqSpeed>{max(1, min(40, speed))}</seqSpeed>"
            f"<delay>{delay}</delay>"
            "</PatrolSequence>"
        )
    # Pad to 8 empty slots (common ISAPI patrol length).
    while len(sequences) < 8:
        sequences.append(
            "<PatrolSequence>"
            "<presetID>0</presetID>"
            "<seqSpeed>30</seqSpeed>"
            "<delay>15</delay>"
            "</PatrolSequence>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PTZPatrol version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        f"<enabled>{'true' if enabled else 'false'}</enabled>"
        f"<id>{int(tour_id)}</id>"
        f"<patrolName>{_escape_xml(safe_name)}</patrolName>"
        f"<PatrolSequenceList>{''.join(sequences)}</PatrolSequenceList>"
        "</PTZPatrol>"
    )
    return body.encode("utf-8")


async def list_tours(camera: dict) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    status, text = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/patrols")
    if status == 404:
        return {
            "ok": False,
            "supported": False,
            "tours": [],
            "error": "Patrols/tours not supported on this camera",
            "backend": "isapi",
        }
    if status != 200:
        return {
            "ok": False,
            "supported": True,
            "status": status,
            "error": _error_from_response(status, text),
            "tours": [],
            "backend": "isapi",
        }
    return {"ok": True, "supported": True, "tours": _parse_patrols_xml(text), "backend": "isapi"}


async def set_tour(
    camera: dict,
    tour_id: int,
    *,
    name: str,
    steps: List[Dict[str, Any]],
    enabled: bool = True,
) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patrols/{int(tour_id)}"
    status, text = await _isapi(
        camera,
        "PUT",
        path,
        body=_patrol_set_xml(tour_id, name, steps, enabled=enabled),
    )
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def delete_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    """Clear a patrol by writing empty sequences (DELETE is often unsupported)."""
    channel = _ptz_channel(camera)
    clear = await set_tour(camera, tour_id, name=str(tour_id), steps=[], enabled=False)
    if clear.get("ok"):
        return clear
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patrols/{int(tour_id)}"
    status, text = await _isapi(camera, "DELETE", path)
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def start_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patrols/{int(tour_id)}/start"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def stop_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patrols/{int(tour_id)}/stop"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


def _parse_patterns_xml(text: str) -> List[Dict[str, Any]]:
    """Parse Hikvision PTZPatternList — recorded pan/tilt/zoom paths (not patrols)."""
    patterns: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return patterns

    for node in root.iter():
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
        if tag != "PTZPattern":
            continue
        pattern_id: Optional[int] = None
        name = ""
        enabled = True
        for child in node:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            val = (child.text or "").strip()
            if child_tag == "id" and val.isdigit():
                pattern_id = int(val)
            elif child_tag in ("patternName", "name"):
                name = val
            elif child_tag == "enabled":
                enabled = val.lower() in ("true", "1", "yes")
        if pattern_id is not None:
            patterns.append(
                {
                    "id": pattern_id,
                    "name": name or f"Pattern {pattern_id}",
                    "enabled": enabled,
                }
            )
    patterns.sort(key=lambda p: p["id"])
    return patterns


def _pattern_set_xml(pattern_id: int, name: str) -> bytes:
    safe_name = (name or f"Pattern {pattern_id}").strip()[:64]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PTZPattern version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        f"<id>{int(pattern_id)}</id>"
        f"<patternName>{_escape_xml(safe_name)}</patternName>"
        "</PTZPattern>"
    )
    return body.encode("utf-8")


async def list_patterns(camera: dict) -> Dict[str, Any]:
    """List recorded PTZ patterns (ISAPI) — distinct from patrols/tours."""
    channel = _ptz_channel(camera)
    status, text = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/patterns")
    if status == 404:
        return {
            "ok": False,
            "supported": False,
            "patterns": [],
            "error": "Patterns not supported on this camera (distinct from tours/patrols)",
            "backend": "isapi",
        }
    if status != 200:
        return {
            "ok": False,
            "supported": True,
            "status": status,
            "error": _error_from_response(status, text),
            "patterns": [],
            "backend": "isapi",
        }
    return {
        "ok": True,
        "supported": True,
        "patterns": _parse_patterns_xml(text),
        "backend": "isapi",
        "rdso_18_2_23": True,
        "distinct_from_tour_patrol": True,
    }


async def set_pattern(camera: dict, pattern_id: int, *, name: str) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}"
    status, text = await _isapi(camera, "PUT", path, body=_pattern_set_xml(pattern_id, name))
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def delete_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}"
    status, text = await _isapi(camera, "DELETE", path)
    if status not in (200, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def start_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}/start"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def stop_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}/stop"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def record_pattern_start(camera: dict, pattern_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}/recordstart"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def record_pattern_stop(camera: dict, pattern_id: int) -> Dict[str, Any]:
    channel = _ptz_channel(camera)
    path = f"/ISAPI/PTZCtrl/channels/{channel}/patterns/{int(pattern_id)}/recordstop"
    status, text = await _isapi(camera, "PUT", path, body=b"")
    if status not in (200, 201, 204):
        return {"ok": False, "status": status, "error": _error_from_response(status, text), "backend": "isapi"}
    return {"ok": True, "backend": "isapi"}


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    """Probe ISAPI PTZ + whether presets/patrols/patterns respond."""
    channel = _ptz_channel(camera)
    status, text = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/capabilities")
    ptz_ok = status == 200
    if not ptz_ok:
        status2, text2 = await _isapi(camera, "GET", "/ISAPI/PTZCtrl/channels")
        ptz_ok = status2 == 200 and "PTZChannel" in text2
        if not ptz_ok:
            return {
                "ok": False,
                "supported": False,
                "presetsSupported": False,
                "toursSupported": False,
                "patternsSupported": False,
                "error": _error_from_response(status2, text2),
                "backend": "isapi",
            }

    presets_status, _ = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/presets")
    tours_status, _ = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/patrols")
    patterns_status, _ = await _isapi(camera, "GET", f"/ISAPI/PTZCtrl/channels/{channel}/patterns")
    patterns_ok = patterns_status == 200
    return {
        "ok": True,
        "supported": True,
        "backend": "isapi",
        "presetsSupported": presets_status == 200,
        "toursSupported": tours_status == 200,
        "patternsSupported": patterns_ok,
        "rdso_18_2_23": patterns_ok,
        "patternDistinctFromTourPatrol": True,
        "presets": {
            "list": presets_status == 200,
            "set": True,
            "goto": True,
            "delete": True,
        },
        "tours": {
            "list": tours_status == 200,
            "set": tours_status == 200,
            "start": tours_status == 200,
            "stop": tours_status == 200,
            "delete": tours_status == 200,
        },
        "patterns": {
            "list": patterns_ok,
            "set": patterns_ok,
            "start": patterns_ok,
            "stop": patterns_ok,
            "record": patterns_ok,
            "delete": patterns_ok,
        },
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
