"""RDSO 18.6 — CCC status aggregation (reuses existing VMS health surfaces)."""

from __future__ import annotations

from typing import Any, Optional


async def get_ccc_status_snapshot(*, user: Optional[dict] = None) -> dict[str, Any]:
    """Best-effort status for CCC overview — never fails closed on optional probes."""
    out: dict[str, Any] = {
        "rdso_18_6_18_3": True,
        "ok": True,
        "vms": {"source_id": "local", "label": "Local VMS"},
        "cameras": {},
        "recording": {},
        "go2rtc": {},
        "users_sessions": {},
        "vms_ha": {},
        "recording_ha": {},
    }

    try:
        from app.core.database import camera_collection, user_collection
        from app.services.session_service import count_active_sessions

        out["cameras"] = {
            "total": int(await camera_collection.count_documents({})),
            "active": int(
                await camera_collection.count_documents(
                    {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
                )
            ),
        }
        out["users_sessions"] = {
            "users": int(await user_collection.count_documents({})),
            "active_sessions": int(await count_active_sessions()),
        }
    except Exception as exc:
        out["cameras"] = {"error": str(exc)}
        out["ok"] = False

    try:
        from app.services.recording_config import get_recording_capacity_info

        out["recording"] = get_recording_capacity_info()
    except Exception as exc:
        out["recording"] = {"error": str(exc)}

    try:
        from app.services import go2rtc_workers as gw

        workers = None
        if hasattr(gw, "WORKER_COUNT"):
            workers = int(getattr(gw, "WORKER_COUNT") or 1)
        elif hasattr(gw, "needed_workers_for_camera_count"):
            workers = 1
        out["go2rtc"] = {
            "workers": workers,
            "browser_path": "/media/w{N}/api/ws",
            "direct_camera": False,
        }
    except Exception:
        out["go2rtc"] = {"browser_path": "/media/w{N}/api/ws", "direct_camera": False}

    try:
        from app.services.vms_server_config import vms_ha_enabled, local_vms_server_id

        out["vms_ha"] = {
            "enabled": bool(vms_ha_enabled()),
            "local_server_id": local_vms_server_id(),
            "reuses_existing_vms_ha": True,
        }
    except Exception:
        out["vms_ha"] = {"reuses_existing_vms_ha": True, "available": False}

    try:
        from app.services.recording_server_config import (
            recording_ha_enabled,
            local_recording_server_id,
        )

        out["recording_ha"] = {
            "enabled": bool(recording_ha_enabled()),
            "local_server_id": local_recording_server_id(),
            "reuses_existing_recording_ha": True,
        }
    except Exception:
        out["recording_ha"] = {"reuses_existing_recording_ha": True, "available": False}

    out["management_links"] = {
        "cameras": "/camera-management",
        "users": "/user-management",
        "live": "/live",
        "playback": "/playback",
        "events": "/events",
        "system_status": "/system-status",
    }
    out["actor"] = {
        "id": str((user or {}).get("id") or (user or {}).get("_id") or ""),
        "role": (user or {}).get("role") or "",
    }
    return out
