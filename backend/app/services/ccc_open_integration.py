"""RDSO 18.6.22.8 — open CCC third-party integration contract + helpers.

Thin facade over existing CCC device + VMS integration stacks.
Does not create a second event/alarm engine or GIS store.
"""

from __future__ import annotations

from typing import Any, Optional


OPEN_INTEGRATION_CONTRACT_VERSION = "18.6.22.8"


def extract_location_metadata(body: dict[str, Any]) -> dict[str, Any]:
    """Preserve location in a neutral form for future GIS — not GIS-format storage.

    Accepts location/zone/site strings and optional lat/lon/alt fields.
    Sets gis_format_stored=False so clients do not assume RDSO GIS completion.
    """
    out: dict[str, Any] = {}
    if not isinstance(body, dict):
        return out
    loc = body.get("location") or body.get("zone") or body.get("site") or body.get("place")
    if loc not in (None, ""):
        out["location"] = str(loc).strip()[:500]

    lat = body.get("latitude", body.get("lat"))
    lon = body.get("longitude", body.get("lon", body.get("lng")))
    alt = body.get("altitude", body.get("alt"))
    geo: dict[str, Any] = {}
    try:
        if lat is not None and lat != "" and lon is not None and lon != "":
            geo["lat"] = float(lat)
            geo["lon"] = float(lon)
            if alt is not None and alt != "":
                geo["alt"] = float(alt)
    except (TypeError, ValueError):
        geo = {}
    if geo:
        out["geo"] = geo
        # Honest: coordinates preserved as metadata only — GIS format / Site Maps deferred.
        out["gis_format_stored"] = False
        out["gis_deferred"] = True
    return out


def open_integration_contract() -> dict[str, Any]:
    """Purchaser-facing common contract for third-party systems/devices."""
    return {
        "rdso_18_6_22_8": True,
        "contract_version": OPEN_INTEGRATION_CONTRACT_VERSION,
        "title": "CCC open third-party integration interface",
        "second_alarm_engine": False,
        "named_vendor_sdks_bundled": False,
        "fake_vendors": False,
        "gis": False,
        "gis_format_storage": False,
        "gis_note": (
            "Location/coordinates may be sent as metadata (location, lat/lon). "
            "GIS-format storage and Site Maps remain deferred (18.6.14_gis)."
        ),
        "pipeline": "event → CCC Event Log → Hot Screen → Incident workflow",
        "reuses": {
            "devices": "/api/ccc/devices",
            "device_ingest": "/api/ccc/devices/{id}/ingest",
            "device_heartbeat": "/api/ccc/devices/{id}/heartbeat",
            "vms_admin": "/api/ccc/admin/vms-integrations",
            "vms_alerts": "/api/ccc/vms-integrations/{source_id}/alerts",
            "vms_adapters": "VmsVideoSource / GenericRestVmsSource",
            "events": "event_service.create_event",
        },
        "facade": {
            "capability": "/api/ccc/integrations/open/capability",
            "identity": "/api/ccc/integrations/open/identity",
            "register": "/api/ccc/integrations/open/register",
            "health": "/api/ccc/integrations/open/health",
            "devices": "/api/ccc/integrations/open/devices",
            "events": "/api/ccc/integrations/open/events",
        },
        "fields": {
            "source_system_identity": ["source_id", "device_id", "device_uid", "kind"],
            "health_status": ["status", "health", "kind=heartbeat|status|health"],
            "devices": ["type", "label", "linked_camera_ids", "enabled"],
            "events_alerts": [
                "title",
                "message",
                "severity",
                "priority",
                "source_type",
                "occurred_at|timestamp",
                "idempotency_key|external_event_id",
                "metadata",
            ],
            "capabilities": ["live", "playback", "events", "health"],
            "timestamps": ["occurred_at", "timestamp", "ISO-8601"],
            "location": [
                "location|zone",
                "latitude|lat",
                "longitude|lon|lng",
                "altitude|alt (optional)",
            ],
            "optional_live_playback": "via existing VMS adapters when capability enabled",
        },
        "limits": {
            "body_max_bytes": 16384,
            "metadata_max_keys": 32,
            "rate_window_seconds_device": 10,
            "rate_max_per_window_device": 60,
        },
        "auth": {
            "admin_config": "session Admin/SuperAdmin",
            "ingest": "Bearer integration secret or X-CCC-Device-Secret / X-VMS-Integration-Secret",
            "credentials_never_returned": True,
            "tls_ready": True,
        },
        "docs": {
            "guide": "/api/docs/integration",
            "openapi": "/api/docs/integration/openapi.json",
        },
    }


def open_integration_identity() -> dict[str, Any]:
    return {
        "system": "ccc",
        "product": "CCTV VMS / Centralized Command Center",
        "rdso_18_6_22_8": True,
        "browser_entry": "/ccc",
        "api_surface": "/api/ccc/*",
        "open_facade": "/api/ccc/integrations/open/*",
        "named_vendor_sdks_bundled": False,
        "gis_format_storage": False,
    }
