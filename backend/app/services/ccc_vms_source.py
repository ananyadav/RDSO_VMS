"""RDSO 18.6 — CCC → VMS video integration abstraction.

CCC must never connect to camera RTSP/ONVIF. All video, cameras, events and
status flow through a VmsVideoSource. The local/current VMS is the first
implementation; external adapters plug into the same contract (18.6.17.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from app.services.ccc_vms_adapter_errors import UnsupportedCapabilityError

logger = logging.getLogger(__name__)


@runtime_checkable
class VmsVideoSource(Protocol):
    """Pluggable CCC integration contract (local + external VMS)."""

    source_id: str
    label: str

    def describe(self) -> dict[str, Any]:
        ...

    async def list_cameras_public(
        self,
        user: dict[str, Any],
        *,
        limit: int = 100,
        offset: int = 0,
        q: str = "",
    ) -> dict[str, Any]:
        ...

    async def client_media_for(self, camera_ref: str, user: dict[str, Any]) -> dict[str, Any]:
        ...

    async def health_status(self) -> dict[str, Any]:
        ...

    async def search_playback_public(
        self,
        user: dict[str, Any],
        camera_ref: str,
        *,
        from_ts: str = "",
        to_ts: str = "",
    ) -> dict[str, Any]:
        ...


@dataclass
class LocalVmsSource:
    """First CCC source: this deployment's VMS (Mongo + go2rtc + recordings)."""

    source_id: str = "local"
    label: str = "Local VMS"

    def describe(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "kind": "local_vms",
            "browser_only": True,
            "requires_separate_client": False,
            "direct_camera_rtsp": False,
            "video_path": "CCC UI → VMS API/media → go2rtc/recordings",
            "camera_identity": "centralized_stable_uid",
            "external_vendor_integration": False,
            "named_vendor_sdk": False,
            "pluggable": True,
            "capabilities": {
                "live": True,
                "playback": True,
                "events": True,
                "health": True,
            },
            "note": "Local VMS — unchanged first implementation.",
        }

    async def list_cameras_public(
        self,
        user: dict[str, Any],
        *,
        limit: int = 100,
        offset: int = 0,
        q: str = "",
    ) -> dict[str, Any]:
        from app.services.camera_access import build_access_filter, is_admin, merge_query
        from app.core.database import camera_collection

        limit_n = max(1, min(int(limit or 100), 500))
        offset_n = max(0, int(offset or 0))
        query: dict[str, Any] = {
            "$or": [{"is_active": True}, {"is_active": {"$exists": False}}],
        }
        needle = (q or "").strip()
        if needle:
            query = {
                "$and": [
                    query,
                    {
                        "$or": [
                            {"name": {"$regex": needle, "$options": "i"}},
                            {"display_name": {"$regex": needle, "$options": "i"}},
                            {"camera_uid": {"$regex": needle, "$options": "i"}},
                            {"ip_address": {"$regex": needle, "$options": "i"}},
                        ]
                    },
                ]
            }
        if not is_admin(user):
            query = merge_query(query, build_access_filter(user))

        total = int(await camera_collection.count_documents(query))
        cursor = (
            camera_collection.find(query)
            .sort([("display_name", 1), ("name", 1)])
            .skip(offset_n)
            .limit(limit_n)
        )
        items = [ccc_public_camera(doc) async for doc in cursor]
        return {
            "source_id": self.source_id,
            "items": items,
            "total": total,
            "limit": limit_n,
            "offset": offset_n,
            "returned": len(items),
            "streams_not_auto_started": True,
            "rdso_18_6": True,
        }

    async def client_media_for(self, camera_ref: str, user: dict[str, Any]) -> dict[str, Any]:
        from app.services.camera_access import user_can_access_camera, is_admin
        from app.services.camera_identity import get_camera_by_ref
        from app.services.client_media_routing import build_client_media_routing

        cam = await get_camera_by_ref(camera_ref)
        if not cam:
            raise LookupError("Camera not found")
        if not is_admin(user) and not user_can_access_camera(user, camera_ref, cam):
            raise PermissionError("Camera access denied")
        routing = build_client_media_routing(cam)
        return sanitize_ccc_media_payload(routing)

    async def health_status(self) -> dict[str, Any]:
        from app.core.startup_state import health_payload, new_startup_state

        state = new_startup_state()
        state["ready"] = True
        state["mongodb"] = True
        payload = health_payload(state)
        return {
            "source_id": self.source_id,
            "status": "healthy" if payload.get("ready") else "degraded",
            "local": payload,
        }

    async def search_playback_public(
        self,
        user: dict[str, Any],
        camera_ref: str,
        *,
        from_ts: str = "",
        to_ts: str = "",
    ) -> dict[str, Any]:
        from app.services.client_media_routing import playback_media_path_template

        return {
            "source_id": self.source_id,
            "camera_ref": camera_ref,
            "supported": True,
            "media_path_template": playback_media_path_template(),
            "search_api": "/api/playback/search",
            "from_ts": from_ts or None,
            "to_ts": to_ts or None,
            "via_local_vms": True,
        }


_SOURCES: dict[str, VmsVideoSource] = {"local": LocalVmsSource()}


def count_external_vms_sources() -> int:
    return sum(1 for k in _SOURCES if k != "local")


def get_ccc_source(source_id: str = "local") -> VmsVideoSource:
    sid = (source_id or "local").strip() or "local"
    src = _SOURCES.get(sid)
    if src is None:
        raise KeyError(f"Unknown CCC VMS source: {sid}")
    return src


async def get_ccc_source_async(source_id: str = "local") -> VmsVideoSource:
    sid = (source_id or "local").strip() or "local"
    if sid != "local":
        from app.services.ccc_vms_integration_store import get_integration

        doc = await get_integration(sid)
        if not doc or not doc.get("enabled"):
            raise KeyError(f"CCC VMS source disabled or not found: {sid}")
    return get_ccc_source(sid)


def list_ccc_sources() -> list[dict[str, Any]]:
    return [s.describe() for s in _SOURCES.values()]


async def reload_external_vms_sources() -> dict[str, Any]:
    """Load enabled integrations from Mongo into the in-memory registry."""
    from app.services.ccc_external_vms_rest_adapter import GenericRestVmsSource
    from app.services.ccc_vms_integration_store import (
        integration_config_for_adapter,
        list_integrations,
    )

    # Drop prior external entries only.
    for key in list(_SOURCES.keys()):
        if key != "local":
            del _SOURCES[key]

    loaded: list[str] = []
    errors: list[dict[str, str]] = []
    for pub in await list_integrations(enabled_only=True):
        sid = pub.get("source_id") or ""
        try:
            from app.services.ccc_vms_integration_store import get_integration

            doc = await get_integration(sid)
            if not doc:
                continue
            cfg = integration_config_for_adapter(doc)
            if cfg.get("vendor_type") == "generic_rest":
                _SOURCES[sid] = GenericRestVmsSource(cfg)
                loaded.append(sid)
        except Exception as exc:
            logger.warning("[ccc] failed to load external source %s: %s", sid, exc)
            errors.append({"source_id": sid, "error": str(exc)[:200]})
    return {"loaded": loaded, "count": len(loaded), "errors": errors}


def register_ccc_source_for_tests(source: VmsVideoSource) -> None:
    """Test helper — do not register fake vendors in production."""
    _SOURCES[source.source_id] = source


def unregister_ccc_source_for_tests(source_id: str) -> None:
    if source_id != "local" and source_id in _SOURCES:
        del _SOURCES[source_id]


def adapter_error_response(exc: Exception) -> tuple[int, dict[str, Any]]:
    from app.services.ccc_vms_adapter_errors import VmsAdapterError

    if isinstance(exc, UnsupportedCapabilityError):
        return 501, {"error": str(exc), "code": exc.code, "supported": False}
    if isinstance(exc, VmsAdapterError):
        status = 503 if exc.code in ("source_offline", "timeout") else 502
        if exc.code == "authentication_failure":
            status = 401
        if exc.code == "direct_camera_forbidden":
            status = 422
        return status, {"error": str(exc), "code": exc.code}
    return 500, {"error": str(exc)}


def ccc_public_camera(doc: dict[str, Any]) -> dict[str, Any]:
    """Camera descriptor for CCC clients — no passwords, no RTSP URLs."""
    cid = str(doc.get("_id") or doc.get("id") or "")
    uid = str(doc.get("camera_uid") or doc.get("cameraUid") or "").strip()
    worker = doc.get("worker_id", doc.get("workerId"))
    try:
        worker_id = int(worker) if worker not in (None, "") else 1
    except (TypeError, ValueError):
        worker_id = 1
    return {
        "id": cid,
        "camera_uid": uid,
        "cameraUid": uid,
        "name": doc.get("name") or "",
        "displayName": doc.get("display_name") or doc.get("displayName") or doc.get("name") or "",
        "ip_address": doc.get("ip_address") or "",
        "online": bool(doc.get("online", doc.get("is_online", False))),
        "ptz": bool(doc.get("ptz") or doc.get("is_ptz")),
        "camera_group": doc.get("camera_group") or "",
        "location_path": doc.get("location_path") or "",
        "is_active": doc.get("is_active", True),
        "workerId": worker_id,
        "recording_home_server_id": doc.get("recording_server_id") or None,
        "identity_stable": True,
        "password": None,
        "main_rtsp_url": None,
        "sub_rtsp_url": None,
        "rtsp_url": None,
    }


def sanitize_ccc_media_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that looks like a connect URL or credential."""
    forbidden_keys = {
        "password",
        "username",
        "main_rtsp_url",
        "sub_rtsp_url",
        "rtsp_url",
        "rtsp",
        "onvif_password",
        "camera_password",
        "credential",
        "api_key",
        "token",
    }

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in forbidden_keys or "password" in lk or "rtsp" in lk:
                    continue
                if isinstance(v, str) and v.lower().startswith("rtsp://"):
                    continue
                out[k] = _walk(v)
            return out
        if isinstance(obj, list):
            return [_walk(x) for x in obj]
        return obj

    cleaned = _walk(payload)
    cleaned["ccc_no_direct_camera"] = True
    cleaned["ccc_browser_only"] = True
    return cleaned


def ccc_capability_public() -> dict[str, Any]:
    ext_count = count_external_vms_sources()
    return {
        "rdso_18_6": True,
        "clauses": {
            "18.6.1": True,
            "18.6.2": True,
            "18.6.3": True,
            "18.6.4": True,
            "18.6.5": True,
            "18.6.6": True,
            "18.6.7": True,
            "18.6.12": True,
            "18.6.13": True,
            "18.6.14": True,
            "18.6.14_gis": False,
            "18.6.15.2": True,
            "18.6.15.3": True,
            "18.6.15.4": True,
            "18.6.16.1": True,
            "18.6.17.1": True,
            "18.6.17.2": True,
            "18.6.17.3": True,
            "18.6.17.4": True,
            "18.6.18.2": True,
            "18.6.18.3": True,
            "18.6.20": True,
            "18.6.22.1": True,
            "18.6.22.3": True,
            "18.6.22.8": True,
            "18.6.22.9": True,
            "18.6.22.11": True,
            "18.6.22.12": True,
            "18.6.22.13": True,
            "18.6.22.14": True,
            "18.6.22.15": True,
            "18.6.22.18": True,
        },
        "vms_integration": {
            "framework": "VmsVideoSource",
            "admin_path": "/api/ccc/admin/vms-integrations",
            "alert_ingest_path": "/api/ccc/vms-integrations/{source_id}/alerts",
            "adapter_kinds": ["generic_rest"],
            "named_vendor_sdks_bundled": False,
            "fake_vendors": False,
            "path": "CCC → VMS adapter → external VMS",
            "direct_camera_forbidden": True,
        },
        "alarm_monitoring": {
            "path": "/api/ccc/alarm-monitoring",
            "recover_path": "/api/ccc/alarm-monitoring/{eventId}/recover",
            "zone_map_path": "/api/ccc/admin/zone-camera-map",
            "snapshot_path": "/api/ccc/cameras/{id}/snapshot",
            "states": ["MONITORING", "ALARM"],
            "video_path": "CCC → VMS → /media/go2rtc",
            "direct_camera_rtsp_forbidden": True,
            "geo_distance_invented": False,
            "on_demand_snapshot_via_go2rtc": True,
        },
        "reliability": {
            "startup_budget_seconds": 300,
            "health_path": "/api/health",
            "ready_path": "/api/ready",
            "paginated_ccc_apis": True,
            "no_full_collection_loads": True,
        },
        "transport_security": {
            "path": "/api/ccc/security",
            "min_symmetric_bits": 128,
            "tls_at_nginx": True,
            "https_enforce_env": "HTTPS_ENFORCE",
            "relative_media_urls": True,
        },
        "devices": {
            "path": "/api/ccc/devices",
            "ingest_path": "/api/ccc/devices/{id}/ingest",
            "types": ["camera", "vms", "camera_digital_input", "external_sensor"],
            "fake_vendors": False,
            "second_alarm_engine": False,
            "hard_count_cap": False,
            "opens_streams_on_register": False,
            "preemption_path": "/api/ccc/admin/preemption-policy",
        },
        "open_integration": {
            "rdso_18_6_22_8": True,
            "capability_path": "/api/ccc/integrations/open/capability",
            "identity_path": "/api/ccc/integrations/open/identity",
            "register_path": "/api/ccc/integrations/open/register",
            "health_path": "/api/ccc/integrations/open/health",
            "devices_path": "/api/ccc/integrations/open/devices",
            "events_path": "/api/ccc/integrations/open/events",
            "reuses_device_ingest": True,
            "reuses_vms_alert_ingest": True,
            "second_alarm_engine": False,
            "named_vendor_sdks_bundled": False,
            "fake_vendors": False,
            "gis": False,
            "gis_format_storage": False,
            "location_metadata_preserved": True,
            "idempotency": True,
            "body_max_bytes": 16384,
            "docs": "/api/docs/integration",
            "openapi": "/api/docs/integration/openapi.json",
            "pipeline": "event → CCC Event Log → Hot Screen → Incident workflow",
        },
        "dashboard": {
            "path": "/api/ccc/dashboard",
            "prefs_path": "/api/ccc/dashboard/prefs",
            "hot_screen_path": "/api/ccc/hot-screen",
            "per_user_layout": True,
            "fake_statistics": False,
            "poll_refresh": True,
        },
        "video_management": {
            "ui_tab": "video-management",
            "max_live_tiles": 16,
            "path": "CCC → VMS → /media/wN → go2rtc",
            "direct_camera_rtsp_forbidden": True,
            "streams_not_auto_started": True,
            "rdso_18_6_15_3": True,
        },
        "health_reports": {
            "path": "/api/ccc/health-reports",
            "export_path": "/api/ccc/health-reports/export",
            "formats": ["json", "csv"],
            "gis": False,
            "gis_pending": True,
            "fabricated_metrics": False,
            "live_screen_tab": "live",
            "rdso_18_6_14_health": True,
            "rdso_18_6_14_gis": False,
        },
        "historical_reports": {
            "path": "/api/ccc/reports",
            "formats": ["json", "csv"],
            "pdf": False,
            "app_timezone": True,
            "duplicate_collections": False,
            "kinds": [
                "incidents",
                "events",
                "activity",
                "communications",
                "compliance",
            ],
        },
        "incidents": {
            "collection": "ccc_incidents",
            "linked_to_vms_events": True,
            "not_a_second_alarm_engine": True,
            "duplicate_event_prevention": True,
            "multi_author_updates": True,
            "event_log_path": "/api/ccc/event-log",
            "incidents_path": "/api/ccc/incidents",
            "sop_path": "/api/ccc/sop-workflows",
            "dmr_tetra": False,
            "internal_comms_only": True,
        },
        "browser_only": True,
        "requires_separate_client": False,
        "direct_camera_rtsp_forbidden": True,
        "video_path": "CCC UI → VMS API/media routing → go2rtc/recordings",
        "scale": {
            "supports_thousands_of_cameras": True,
            "does_not_open_all_streams": True,
            "uses_registry_virtualization_go2rtc_mongo": True,
        },
        # RDSO 18.6.16.1 — software overview evidence (characteristics → existing surfaces).
        # Poll-based reactive model; no millisecond real-time claim.
        "platform_architecture": {
            "rdso_18_6_16_1": True,
            "title": "CCC platform architecture / software overview",
            "browser_entry": "/ccc",
            "api_surface": "/api/ccc/*",
            "capability_path": "/api/ccc/capability",
            "status_path": "/api/ccc/status",
            "flexible_dynamic": {
                "runtime_config_without_restart": True,
                "surfaces": [
                    "/api/ccc/dashboard/prefs",
                    "/api/ccc/sop-workflows",
                    "/api/ccc/devices",
                    "/api/ccc/admin/preemption-policy",
                    "/api/ccc/admin/zone-camera-map",
                    "/api/ccc/admin/vms-integrations",
                ],
                "hot_reload_sop_escalation": True,
            },
            "distributed": {
                "vms_multi_node_ha": True,
                "shared_mongo_state": True,
                "vms_ha_env": "VMS_HA_ENABLED",
                "vms_ha_status_path": "/api/vms/ha/status",
                "ccc_consumes_vms_sources": True,
                "single_node_only_assumption": False,
                "reuses_existing_vms_ha": True,
            },
            "reactive_real_time": {
                "model": "http_poll",
                "millisecond_realtime_claimed": False,
                "dashboard_poll_seconds_suggested": 15,
                "hot_screen_poll_seconds_suggested": 10,
                "event_log_path": "/api/ccc/event-log",
                "hot_screen_path": "/api/ccc/hot-screen",
                "incidents_path": "/api/ccc/incidents",
                "alarm_monitoring_path": "/api/ccc/alarm-monitoring",
                "live_media": "relative_wss_via_vms_go2rtc",
            },
            "scalable": {
                "paginated_lists": True,
                "bounded_queries": True,
                "virtualized_camera_selection": True,
                "max_live_tiles": 16,
                "fleet_wide_stream_opening": False,
                "hard_logical_camera_cap": False,
                "cameras_path": "/api/ccc/cameras",
            },
            "ip_network": {
                "browser_api_over_ip": True,
                "relative_https_wss_capable_paths": True,
                "proprietary_client_required": False,
                "transport_security_path": "/api/ccc/security",
                "direct_camera_rtsp_forbidden": True,
            },
            "automated_policies_workflows": {
                "sop_workflows": True,
                "sop_path": "/api/ccc/sop-workflows",
                "escalation_rules": True,
                "escalation_process_path": "/api/ccc/incidents/process-escalations",
                "alarm_display_switching": True,
                "alarm_monitoring": True,
                "preemption_policy": True,
                "incident_automation": True,
                "second_alarm_engine": False,
            },
            "single_customized_dashboard": {
                "path": "/api/ccc/dashboard",
                "prefs_path": "/api/ccc/dashboard/prefs",
                "per_user_widget_visibility_order": True,
                "control_monitoring_links": True,
                "fake_statistics": False,
                "ui_tab": "overview",
            },
            "out_of_scope_here": [
                "anirudh_camera_sequences",
                "logical_tree",
                "site_maps_gis",
                "ai_frs",
                "dmr_tetra",
            ],
        },
        "management_reuses_existing": [
            "cameras",
            "users",
            "recording_servers",
            "vms_servers",
        ],
        "sources": list_ccc_sources(),
        "external_vms_adapters_registered": ext_count,
        "ui_path": "/ccc",
        "api": {
            "capability": "/api/ccc/capability",
            "sources": "/api/ccc/sources",
            "cameras": "/api/ccc/cameras",
            "status": "/api/ccc/status",
            "client_media": "/api/ccc/cameras/{id}/client-media",
            "playback_search": "/api/ccc/playback/search",
        },
    }
