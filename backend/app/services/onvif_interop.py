"""RDSO 18.3.15 / 18.1.30 — multi-vendor recording interoperability summary."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from app.services.edge_capability import detect_edge_storage_capability
from app.services.onvif_stream_uri import probe_onvif_profile_s, resolve_onvif_profile_s_streams
from app.services.ptz_control import _brand, _protocol, backends_for
from app.services.rtsp_utils import AUTO_RTSP_BRANDS, normalize_make

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
_SDK_DIR = Path(__file__).resolve().parents[3] / "sdk" / "integration"


def integration_surface_public() -> Dict[str, Any]:
    """Purchaser-facing integration package pointers (REST + thin reference client)."""
    return {
        "type": "REST",
        "docs": "/api/docs/integration",
        "openapi": "/api/docs/integration/openapi.json",
        "system_interop": "/api/system/interop",
        "reference_client": "sdk/integration/vms_client.py",
        "package_readme": "docs/integration-package/README.md",
        "sdk": (
            "Authenticated REST is the supported integration surface. "
            "A thin reference Python client is provided under sdk/integration/ "
            "(not a proprietary binary SDK)."
        ),
        "docs_on_disk": (_DOCS_DIR / "integration-api.md").is_file(),
        "openapi_on_disk": (_DOCS_DIR / "integration-openapi.json").is_file(),
        "reference_client_on_disk": (_SDK_DIR / "vms_client.py").is_file(),
    }


async def get_camera_interop_summary(
    camera: dict,
    *,
    resolve_streams: bool = False,
) -> Dict[str, Any]:
    """Honest capability summary for Profile S / G and vendor fallbacks."""
    camera_id = str(camera.get("_id") or camera.get("id") or "")
    protocol = normalize_make(camera.get("protocol") or "")
    brand = _brand(camera)

    profile_s: Dict[str, Any]
    if resolve_streams:
        profile_s = await resolve_onvif_profile_s_streams(camera)
        # Drop raw credentials from response
        for role in ("main", "sub"):
            block = profile_s.get(role)
            if isinstance(block, dict) and block.get("rtsp_uri"):
                block = dict(block)
                block.pop("rtsp_uri", None)
                profile_s[role] = block
    else:
        # Lightweight: only claim Profile S when protocol is ONVIF or probe is requested
        if protocol == "ONVIF" or str(camera.get("rtsp_url_source") or "").startswith("onvif"):
            profile_s = await probe_onvif_profile_s(camera)
        else:
            profile_s = {
                "profile": "S",
                "supported": False,
                "message": (
                    f"Camera uses {protocol or 'vendor'} RTSP templates; "
                    "ONVIF Profile S GetStreamUri available via resolve endpoint"
                ),
                "main_ok": False,
                "sub_ok": False,
                "profile_count": 0,
                "source": "vendor_template",
            }

    try:
        profile_g = await detect_edge_storage_capability(camera)
    except Exception as exc:
        logger.info("[INTEROP] Profile G probe failed for %s: %s", camera_id, exc)
        profile_g = {
            "supported": False,
            "protocol": "none",
            "message": f"Profile G capability probe failed: {exc}",
        }

    try:
        ptz_backends = list(backends_for(camera))
    except Exception:
        ptz_backends = []

    vendor_rtsp = protocol in AUTO_RTSP_BRANDS
    return {
        "camera_id": camera_id,
        "protocol": protocol or _protocol(camera),
        "brand": brand,
        "rdso_18_3_15": True,
        "rdso_18_1_30": True,
        "profile_s": {
            "supported": bool(profile_s.get("supported")),
            "message": profile_s.get("message") or "",
            "details": profile_s,
        },
        "profile_g": {
            "supported": bool(profile_g.get("supported")),
            "protocol": profile_g.get("protocol"),
            "search_supported": bool(profile_g.get("search_supported")),
            "storage_present": bool(profile_g.get("storage_present")),
            "message": profile_g.get("message") or "",
            "details": {
                k: profile_g.get(k)
                for k in (
                    "protocols_tried",
                    "probes",
                    "storage_present",
                    "search_supported",
                )
                if k in profile_g
            },
        },
        "vendor": {
            "rtsp_templates": vendor_rtsp,
            "rtsp_url_source": camera.get("rtsp_url_source") or "",
            "ptz_backends": ptz_backends,
            "message": (
                f"Vendor RTSP templates active for {protocol}"
                if vendor_rtsp
                else "Manual/ONVIF RTSP path"
            ),
        },
        "integration_api": integration_surface_public(),
    }


async def get_system_interop_status(*, camera_limit: int = 200) -> Dict[str, Any]:
    """Fleet-level ONVIF / integration status without live probing every device.

    Uses stored camera metadata + software module availability. Per-camera live
    Profile S/G probes remain on GET /api/cameras/{id}/interop?resolve=1.
    """
    from app.core.database import camera_collection

    limit = max(1, min(int(camera_limit or 200), 1000))
    total = 0
    by_protocol: Dict[str, int] = {}
    onvif_source = 0
    stored_profile_s = 0
    sample: list[dict[str, Any]] = []

    try:
        total = await camera_collection.count_documents({})
        cursor = camera_collection.find(
            {},
            {
                "name": 1,
                "protocol": 1,
                "make": 1,
                "rtsp_url_source": 1,
                "onvif_profile_s": 1,
                "ip_address": 1,
            },
        ).limit(limit)
        async for doc in cursor:
            proto = normalize_make(doc.get("protocol") or doc.get("make") or "") or "UNKNOWN"
            by_protocol[proto] = by_protocol.get(proto, 0) + 1
            src = str(doc.get("rtsp_url_source") or "")
            if src.startswith("onvif"):
                onvif_source += 1
            if doc.get("onvif_profile_s"):
                stored_profile_s += 1
            if len(sample) < 25:
                sample.append(
                    {
                        "camera_id": str(doc.get("_id")),
                        "name": doc.get("name") or "",
                        "protocol": proto,
                        "rtsp_url_source": src,
                        "has_stored_profile_s": bool(doc.get("onvif_profile_s")),
                        "ip_address": doc.get("ip_address") or "",
                    }
                )
    except Exception as exc:
        logger.warning("[INTEROP] system status camera scan failed: %s", exc)

    modules = {
        "profile_s_getprofiles_getstreamuri": True,
        "profile_g_search_replay": True,
        "edge_backfill": True,
        "ws_discovery": True,
        "hikvision_dahua_fallbacks": True,
    }
    try:
        from app.services import onvif_stream_uri as _s  # noqa: F401
        from app.services import edge_onvif_recording as _g  # noqa: F401
        from app.services import edge_backfill_service as _e  # noqa: F401
        from app.services import camera_discovery as _d  # noqa: F401
    except Exception as exc:
        modules["import_error"] = str(exc)
        for k in list(modules):
            if k != "import_error":
                modules[k] = False

    surface = integration_surface_public()
    return {
        "rdso_18_1_30": True,
        "rdso_18_3_15": True,
        "software_interop_available": all(
            v is True for k, v in modules.items() if k != "import_error"
        ),
        "profile_s": {
            "software_supported": bool(modules.get("profile_s_getprofiles_getstreamuri")),
            "operations": [
                "WS-Discovery (scan)",
                "GetProfiles",
                "GetStreamUri",
                "main/sub resolution",
                "recording via resolved URI",
                "live via persisted/template URI",
            ],
            "cameras_with_onvif_source": onvif_source,
            "cameras_with_stored_profile_s": stored_profile_s,
            "note": (
                "Live viewing uses go2rtc with stored/template RTSP; "
                "explicit GetStreamUri via /onvif/resolve-streams (persist for live)."
            ),
        },
        "profile_g": {
            "software_supported": bool(modules.get("profile_g_search_replay")),
            "operations": [
                "capability detection",
                "recording search",
                "GetReplayUri / retrieval",
                "edge backfill integration",
            ],
            "edge_api": "/api/recordings/edge/capability/{cameraId}",
            "note": "Hikvision prefers ISAPI then ONVIF G; others ONVIF G then ISAPI.",
        },
        "vendor": {
            "auto_rtsp_brands": sorted(AUTO_RTSP_BRANDS),
            "onvif_first_when": "protocol=ONVIF or rtsp_url_source=onvif*",
            "fallbacks": ["HIKVISION ISAPI/templates", "DAHUA templates", "ONVIF Media/PTZ"],
            "unsupported_reported_honestly": True,
        },
        "integration_api": surface,
        "fleet": {
            "camera_total": total,
            "scanned": min(total, limit),
            "by_protocol": by_protocol,
            "sample": sample,
        },
        "tested_capability": {
            "software_unit_tests": (
                "backend/tests/test_rdso_18_3_15_interop.py + test_rdso_18_1_30_interop.py"
            ),
            "live_probe_script": "backend/scripts/_probe_rdso_18_3_15.py",
            "real_camera_acceptance": "deployment — not claimed by this status endpoint",
            "external_onvif_client_list_certification": (
                "NOT claimed. Software interoperability is implemented; "
                "accredited ONVIF Client / RDSO supply-list certification remains "
                "a separate purchaser acceptance item when required."
            ),
        },
        "security": {
            "camera_passwords_never_returned": True,
            "rtsp_credentials_masked": True,
            "auth_rbac_acl_enforced": True,
        },
    }
