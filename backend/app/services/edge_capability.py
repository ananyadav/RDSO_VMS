"""Camera edge-storage capability detection (RDSO 18.3.16)."""

from __future__ import annotations

import logging
from typing import Any

from app.services.edge_isapi import probe_isapi_edge_storage
from app.services.edge_onvif_recording import probe_onvif_profile_g
from app.services.edge_storage_types import PROTOCOL_ISAPI, PROTOCOL_NONE, PROTOCOL_ONVIF_G
from app.services.ptz_control import _brand, _protocol

logger = logging.getLogger(__name__)

HIK_PROTOCOLS = frozenset({"HIKVISION", "HIK"})


def _prefer_isapi(camera: dict) -> bool:
    proto = _protocol(camera)
    brand = _brand(camera)
    if proto in HIK_PROTOCOLS:
        return True
    if "HIKVISION" in brand or brand.startswith("HIK"):
        return True
    return False


async def detect_edge_storage_capability(camera: dict) -> dict[str, Any]:
    """Probe real device APIs — never claim support without evidence."""
    camera_id = str(camera.get("_id") or camera.get("id") or "")
    base: dict[str, Any] = {
        "camera_id": camera_id,
        "supported": False,
        "protocol": PROTOCOL_NONE,
        "protocols_tried": [],
        "storage_present": False,
        "search_supported": False,
        "message": "Edge storage retrieval not available on this camera",
        "probes": {},
    }

    # Optional explicit override for lab/tests only
    override = (camera.get("edge_storage_protocol") or "").strip().lower()
    if override in ("none", "unsupported", "disabled"):
        base["message"] = "Edge storage disabled for this camera"
        return base

    probes: list[tuple[str, Any]] = []
    if override == "isapi" or (not override and _prefer_isapi(camera)):
        probes.append(("isapi", probe_isapi_edge_storage))
        probes.append(("onvif_g", probe_onvif_profile_g))
    elif override in ("onvif", "onvif_g", "profile_g"):
        probes.append(("onvif_g", probe_onvif_profile_g))
        probes.append(("isapi", probe_isapi_edge_storage))
    else:
        # Generic / ONVIF-first, then ISAPI (some OEMs)
        probes.append(("onvif_g", probe_onvif_profile_g))
        probes.append(("isapi", probe_isapi_edge_storage))

    for name, fn in probes:
        base["protocols_tried"].append(name)
        try:
            probe = await fn(camera)
        except Exception as exc:
            logger.info("[EDGE] capability probe %s failed: %s", name, exc)
            base["probes"][name] = {"supported": False, "message": str(exc)}
            continue
        base["probes"][name] = probe
        if probe.get("supported"):
            base["supported"] = True
            base["protocol"] = probe.get("protocol") or (
                PROTOCOL_ISAPI if name == "isapi" else PROTOCOL_ONVIF_G
            )
            base["storage_present"] = bool(probe.get("storage_present"))
            base["search_supported"] = bool(probe.get("search_supported"))
            base["message"] = probe.get("message") or "Edge retrieval supported"
            return base

    # Summarize why unsupported
    messages = []
    for name, probe in (base.get("probes") or {}).items():
        if isinstance(probe, dict) and probe.get("message"):
            messages.append(f"{name}: {probe['message']}")
    if messages:
        base["message"] = "; ".join(messages)
    return base
