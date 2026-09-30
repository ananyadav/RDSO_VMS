"""RDSO 18.1.11.8 / 18.1.18 — settings catalog from existing stores (no duplicate persistence)."""

from __future__ import annotations

from typing import Any, Optional

from app.services.audit_service import redact_value
from app.services.recording_config import (
    RECORDING_AUDIO_ENABLED,
    RECORDING_MAX_CONCURRENT,
    RECORDING_SEGMENT_SECONDS,
    RECORDING_STREAM,
    get_retention_policy,
    is_recording_engine_enabled,
)
from app.services.recording_server_config import (
    local_recording_server_id,
    local_server_role,
    recording_ha_enabled,
)
from app.services.vms_server_config import (
    local_vms_role,
    local_vms_server_id,
    vms_ha_enabled,
)
from app.services.app_timezone import get_effective_app_timezone_name
from app.services.storage_settings_store import get_storage_settings_public


SCOPE_VMS = "vms_server"
SCOPE_RECORDING = "recording_server"
SCOPE_CAMERA = "camera"
SCOPE_CLIENT = "client"

SCOPES = frozenset({SCOPE_VMS, SCOPE_RECORDING, SCOPE_CAMERA, SCOPE_CLIENT})


def _entry(
    *,
    key: str,
    name: str,
    value: Any,
    scope: str,
    source: str,
    editable: bool,
    restart_required: bool = False,
    validation: Optional[str] = None,
    notes: Optional[str] = None,
    manage_path: Optional[str] = None,
) -> dict[str, Any]:
    # Never leak secrets even if a caller passes one by mistake.
    safe_value = redact_value(key, value)
    if isinstance(safe_value, (dict, list)):
        safe_value = str(safe_value)
    return {
        "key": key,
        "name": name,
        "value": safe_value,
        "scope": scope,
        "source": source,
        "editable": bool(editable),
        "restart_required": bool(restart_required),
        "validation": validation,
        "notes": notes,
        "manage_path": manage_path,
    }


def _bool_label(flag: bool) -> str:
    return "enabled" if flag else "disabled"


def build_settings_catalog(*, camera_count: int = 0, master_enabled: Optional[bool] = None) -> dict[str, Any]:
    """Catalog current configuration across VMS, recording, camera, and client scopes."""
    storage = get_storage_settings_public()
    retention = get_retention_policy()
    tz = get_effective_app_timezone_name()
    items: list[dict[str, Any]] = []

    # --- VMS / server ---
    items.append(
        _entry(
            key="vms.server_id",
            name="VMS server ID",
            value=local_vms_server_id(),
            scope=SCOPE_VMS,
            source="env:VMS_SERVER_ID",
            editable=False,
            restart_required=True,
            notes="RDSO 18.1.29 management-node identity. Distinct per VMS process when HA enabled.",
            manage_path="/system-settings",
        )
    )
    items.append(
        _entry(
            key="vms.server_role",
            name="VMS server role",
            value=local_vms_role(),
            scope=SCOPE_VMS,
            source="env:VMS_SERVER_ROLE",
            editable=False,
            restart_required=True,
            validation="primary|standby",
        )
    )
    items.append(
        _entry(
            key="vms.ha_enabled",
            name="VMS management HA",
            value=_bool_label(vms_ha_enabled()),
            scope=SCOPE_VMS,
            source="env:VMS_HA_ENABLED",
            editable=False,
            restart_required=True,
            notes="Leader lease for singleton jobs. NVR redundancy stays on RECORDING_HA_* (18.3.3).",
        )
    )
    items.append(
        _entry(
            key="app.timezone",
            name="Application timezone",
            value=tz,
            scope=SCOPE_VMS,
            source="env:APP_TIMEZONE",
            editable=False,
            restart_required=True,
            validation="IANA timezone name (e.g. Asia/Kolkata)",
            notes="Used for playback calendar days. Change via APP_TIMEZONE then restart API.",
            manage_path="/system-settings",
        )
    )
    items.append(
        _entry(
            key="recording.engine_enabled",
            name="Recording engine",
            value=_bool_label(is_recording_engine_enabled()),
            scope=SCOPE_VMS,
            source="env:RECORDING_ENABLED",
            editable=False,
            restart_required=True,
            validation="true|false",
            notes="Master create-new-recordings flag from environment.",
            manage_path="/storage",
        )
    )
    items.append(
        _entry(
            key="storage.retention_days",
            name="Recording retention (days)",
            value=storage.get("retention_days"),
            scope=SCOPE_VMS,
            source=f"system_settings.storage / {retention.get('source')}",
            editable=bool(storage.get("retention_editable")),
            restart_required=False,
            validation="positive number of days",
            manage_path="/storage",
        )
    )
    items.append(
        _entry(
            key="storage.recordings_dir",
            name="Recordings folder",
            value=storage.get("recordings_dir"),
            scope=SCOPE_VMS,
            source="system_settings.storage",
            editable=bool(storage.get("recordings_dir_editable")),
            restart_required=False,
            validation="absolute writable path on this host",
            notes="Path must be writable; secrets never stored here.",
            manage_path="/storage",
        )
    )
    items.append(
        _entry(
            key="storage.status",
            name="Storage status",
            value=storage.get("storage_status_label") or storage.get("storage_status"),
            scope=SCOPE_VMS,
            source="runtime probe",
            editable=False,
            restart_required=False,
            manage_path="/storage",
        )
    )

    # --- Recording server ---
    items.append(
        _entry(
            key="recording.server_id",
            name="Recording server ID",
            value=local_recording_server_id(),
            scope=SCOPE_RECORDING,
            source="env:RECORDING_SERVER_ID",
            editable=False,
            restart_required=True,
            manage_path="/system-settings",
        )
    )
    items.append(
        _entry(
            key="recording.server_role",
            name="Recording server role",
            value=local_server_role(),
            scope=SCOPE_RECORDING,
            source="env:RECORDING_SERVER_ROLE",
            editable=False,
            restart_required=True,
        )
    )
    items.append(
        _entry(
            key="recording.ha_enabled",
            name="Recording HA coordination",
            value=_bool_label(recording_ha_enabled()),
            scope=SCOPE_RECORDING,
            source="env:RECORDING_HA_ENABLED",
            editable=False,
            restart_required=True,
            notes="Multi-host HA is env-configured.",
        )
    )
    if master_enabled is not None:
        items.append(
            _entry(
                key="recording.master_enabled",
                name="Master recording switch",
                value=_bool_label(bool(master_enabled)),
                scope=SCOPE_RECORDING,
                source="system_settings.recording",
                editable=True,
                restart_required=False,
                manage_path="/storage",
            )
        )
    items.append(
        _entry(
            key="recording.stream_profile",
            name="Default recording stream",
            value=RECORDING_STREAM,
            scope=SCOPE_RECORDING,
            source="env:RECORDING_STREAM",
            editable=False,
            restart_required=True,
            validation="main|sub",
        )
    )
    items.append(
        _entry(
            key="recording.segment_seconds",
            name="HLS segment length (seconds)",
            value=str(RECORDING_SEGMENT_SECONDS),
            scope=SCOPE_RECORDING,
            source="env:RECORDING_HLS_SEGMENT_SECONDS",
            editable=False,
            restart_required=True,
        )
    )
    items.append(
        _entry(
            key="recording.max_concurrent",
            name="Max concurrent recordings",
            value=RECORDING_MAX_CONCURRENT if RECORDING_MAX_CONCURRENT > 0 else "unlimited",
            scope=SCOPE_RECORDING,
            source="env:RECORDING_MAX_CONCURRENT",
            editable=False,
            restart_required=True,
            notes="0 = unlimited (no software soft-cap).",
        )
    )
    items.append(
        _entry(
            key="recording.audio_enabled",
            name="Recording audio",
            value=_bool_label(RECORDING_AUDIO_ENABLED),
            scope=SCOPE_RECORDING,
            source="env:RECORDING_AUDIO_ENABLED",
            editable=False,
            restart_required=True,
        )
    )

    # --- Camera (fleet-level catalog; per-camera managed in Camera Management) ---
    items.append(
        _entry(
            key="camera.fleet_count",
            name="Configured cameras",
            value=camera_count,
            scope=SCOPE_CAMERA,
            source="cameras collection",
            editable=True,
            restart_required=False,
            notes="Add/edit cameras, channels, and stream profiles in Camera Management. Credentials are never returned by the catalog.",
            manage_path="/camera-management",
        )
    )
    items.append(
        _entry(
            key="camera.stream_profiles",
            name="Per-camera stream profiles",
            value="managed per camera",
            scope=SCOPE_CAMERA,
            source="camera + device ISAPI/ONVIF",
            editable=True,
            restart_required=False,
            validation="FPS/resolution within device capability",
            manage_path="/camera-management",
        )
    )
    items.append(
        _entry(
            key="camera.recording_channel",
            name="Per-camera recording channel",
            value="main|sub per camera",
            scope=SCOPE_CAMERA,
            source="camera.recording_channel",
            editable=True,
            restart_required=False,
            manage_path="/camera-management",
        )
    )

    # --- Client / application ---
    from app.services.instant_replay_config import instant_replay_public_config

    ir = instant_replay_public_config()
    items.append(
        _entry(
            key="client.timezone",
            name="Client playback timezone",
            value=tz,
            scope=SCOPE_CLIENT,
            source="env:APP_TIMEZONE via /api/playback/client-config",
            editable=False,
            restart_required=True,
            manage_path="/system-settings",
        )
    )
    items.append(
        _entry(
            key="client.instant_replay",
            name="Instant replay (client)",
            value=ir,
            scope=SCOPE_CLIENT,
            source="env:INSTANT_REPLAY_*",
            editable=False,
            restart_required=True,
            notes="Exposed to authenticated clients without secrets.",
        )
    )
    items.append(
        _entry(
            key="client.ui_theme",
            name="UI theme preference",
            value="browser / user local",
            scope=SCOPE_CLIENT,
            source="browser localStorage",
            editable=True,
            restart_required=False,
            notes="Per-browser; not stored on the VMS server.",
        )
    )

    # Filter accidental secrets from values
    for item in items:
        for secret_key in ("password", "token", "mongodb_uri", "mongo_uri", "secret"):
            if secret_key in str(item.get("key", "")).lower():
                item["value"] = "[REDACTED]"
            if isinstance(item.get("value"), str) and (
                item["value"].lower().startswith("mongodb://")
                or item["value"].lower().startswith("rtsp://")
            ):
                item["value"] = redact_value("value", item["value"])

    by_scope = {s: [] for s in sorted(SCOPES)}
    for item in items:
        by_scope.setdefault(item["scope"], []).append(item)

    return {
        "items": items,
        "by_scope": by_scope,
        "scopes": sorted(SCOPES),
        "total": len(items),
    }


def filter_catalog(catalog: dict[str, Any], *, scope: Optional[str] = None, editable: Optional[bool] = None) -> dict[str, Any]:
    items = list(catalog.get("items") or [])
    if scope:
        sc = scope.strip().lower()
        items = [i for i in items if i.get("scope") == sc]
    if editable is not None:
        items = [i for i in items if bool(i.get("editable")) is bool(editable)]
    return {
        "items": items,
        "scopes": catalog.get("scopes") or sorted(SCOPES),
        "total": len(items),
        "filter": {"scope": scope, "editable": editable},
    }
