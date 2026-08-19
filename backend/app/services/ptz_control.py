"""PTZ control dispatcher — Hikvision ISAPI, ONVIF SOAP, Dahua CGI."""

from __future__ import annotations

from typing import Dict, Sequence

from app.services import dahua_ptz, hikvision_ptz, onvif_ptz

HIK_PROTOCOLS = frozenset({"HIKVISION", "HIK"})
DAHUA_PROTOCOLS = frozenset({"DAHUA"})


def _protocol(camera: dict) -> str:
    return (camera.get("protocol") or "").strip().upper()


def _brand(camera: dict) -> str:
    return (
        (camera.get("brand") or camera.get("model") or camera.get("make") or "")
        .strip()
        .upper()
    )


def backends_for(camera: dict) -> Sequence[str]:
    proto = _protocol(camera)
    brand = _brand(camera)
    if proto in HIK_PROTOCOLS or "HIKVISION" in brand or brand.startswith("HIK"):
        return ("isapi", "onvif")
    if proto in DAHUA_PROTOCOLS or "DAHUA" in brand:
        return ("dahua", "onvif")
    # ONVIF / CUSTOM / UNV / Sparsh / mixed OEM — try ONVIF first, then ISAPI.
    return ("onvif", "isapi")


def _module(name: str):
    if name == "isapi":
        return hikvision_ptz
    if name == "onvif":
        return onvif_ptz
    if name == "dahua":
        return dahua_ptz
    raise ValueError(name)


async def _first_ok(camera: dict, method: str, *args, **kwargs) -> Dict[str, Any]:
    last: Dict[str, Any] = {"ok": False, "error": "PTZ is not available on this camera"}
    for name in backends_for(camera):
        fn = getattr(_module(name), method)
        try:
            result = await fn(camera, *args, **kwargs)
        except Exception as exc:
            last = {"ok": False, "error": str(exc), "backend": name}
            continue
        last = result
        if result.get("ok"):
            result.setdefault("backend", name)
            return result
    return last


async def ptz_continuous(camera: dict, *, pan: int = 0, tilt: int = 0, zoom: int = 0) -> Dict[str, Any]:
    return await _first_ok(camera, "ptz_continuous", pan=pan, tilt=tilt, zoom=zoom)


async def ptz_stop(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "ptz_stop")


async def ptz_move_direction(camera: dict, direction: str, *, speed: int = 2) -> Dict[str, Any]:
    return await _first_ok(camera, "ptz_move_direction", direction, speed=speed)


async def list_presets(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "list_presets")


async def goto_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "goto_preset", preset_id)


async def set_preset(camera: dict, preset_id: int, name: str) -> Dict[str, Any]:
    return await _first_ok(camera, "set_preset", preset_id, name)


async def delete_preset(camera: dict, preset_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "delete_preset", preset_id)


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "ptz_capabilities")
