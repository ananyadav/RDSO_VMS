"""PTZ control dispatcher — Hikvision ISAPI, ONVIF SOAP, Dahua CGI."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

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
        mod = _module(name)
        if not hasattr(mod, method):
            continue
        fn = getattr(mod, method)
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


async def list_tours(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "list_tours")


async def set_tour(
    camera: dict,
    tour_id: int,
    *,
    name: str,
    steps: List[Dict[str, Any]],
    enabled: bool = True,
) -> Dict[str, Any]:
    return await _first_ok(
        camera,
        "set_tour",
        tour_id,
        name=name,
        steps=steps,
        enabled=enabled,
    )


async def delete_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "delete_tour", tour_id)


async def start_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "start_tour", tour_id)


async def stop_tour(camera: dict, tour_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "stop_tour", tour_id)


async def list_patterns(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "list_patterns")


async def set_pattern(camera: dict, pattern_id: int, *, name: str) -> Dict[str, Any]:
    return await _first_ok(camera, "set_pattern", pattern_id, name=name)


async def delete_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "delete_pattern", pattern_id)


async def start_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "start_pattern", pattern_id)


async def stop_pattern(camera: dict, pattern_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "stop_pattern", pattern_id)


async def record_pattern_start(camera: dict, pattern_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "record_pattern_start", pattern_id)


async def record_pattern_stop(camera: dict, pattern_id: int) -> Dict[str, Any]:
    return await _first_ok(camera, "record_pattern_stop", pattern_id)


async def ptz_capabilities(camera: dict) -> Dict[str, Any]:
    return await _first_ok(camera, "ptz_capabilities")
