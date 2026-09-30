"""Per-camera motion/activity recording configuration (Mongo)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection
from app.services.motion_capability import detect_motion_capability
from app.services.motion_recording_types import (
    default_motion_recording_config,
    public_motion_config,
)


def _oid(camera_id: str) -> ObjectId:
    return ObjectId(camera_id)


async def get_motion_recording_settings(camera_id: str) -> dict[str, Any]:
    try:
        cam = await camera_collection.find_one({"_id": _oid(camera_id)})
    except (InvalidId, TypeError):
        raise ValueError("Camera not found")
    if not cam:
        raise ValueError("Camera not found")
    cfg = public_motion_config(cam.get("motion_recording"))
    cap = await detect_motion_capability(cam)
    from app.services.motion_bitrate_validation import evaluate_motion_bitrate_compliance
    from app.services.motion_recording_controller import get_activity_state

    bitrate = await evaluate_motion_bitrate_compliance(
        str(cam["_id"]),
        idle_stream=cfg["idle_stream"],
        active_stream=cfg["active_stream"],
    )
    return {
        "camera_id": str(cam["_id"]),
        "camera_name": cam.get("name") or "",
        "config": cfg,
        "capability": {
            "supported": bool(cap.get("supported")),
            "dual_stream": bool(cap.get("dual_stream")),
            "motion_events": bool(cap.get("motion_events")),
            "protocol": cap.get("protocol"),
            "message": cap.get("message"),
        },
        "bitrate_validation": bitrate,
        "state": get_activity_state(str(cam["_id"])),
    }


async def update_motion_recording_settings(
    camera_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    try:
        cam = await camera_collection.find_one({"_id": _oid(camera_id)})
    except (InvalidId, TypeError):
        raise ValueError("Camera not found")
    if not cam:
        raise ValueError("Camera not found")

    current = public_motion_config(cam.get("motion_recording"))
    if not isinstance(patch, dict):
        raise ValueError("Invalid settings payload")
    merged = {**current, **{k: patch[k] for k in patch if k in current or k in (
        "enabled",
        "active_stream",
        "idle_stream",
        "hold_seconds",
        "cooldown_seconds",
        "motion_source",
    )}}
    cfg = public_motion_config(merged)

    if cfg["enabled"]:
        cap = await detect_motion_capability(cam)
        if not cap.get("supported"):
            raise ValueError(
                cap.get("message")
                or "Motion/activity recording is not supported on this camera"
            )
        from app.services.motion_bitrate_validation import (
            NON_COMPLIANT,
            evaluate_motion_bitrate_compliance,
        )

        bitrate = await evaluate_motion_bitrate_compliance(
            str(cam["_id"]),
            idle_stream=cfg["idle_stream"],
            active_stream=cfg["active_stream"],
        )
        # Refuse enable when measured idle bitrate is NOT lower than active.
        # Unknown bitrates are allowed but compliance is not claimed.
        if bitrate.get("compliance") == NON_COMPLIANT:
            raise ValueError(
                bitrate.get("message")
                or "Idle stream bitrate is not lower than active stream bitrate"
            )

    await camera_collection.update_one(
        {"_id": cam["_id"]},
        {
            "$set": {
                "motion_recording": cfg,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    from app.services.motion_bitrate_validation import evaluate_motion_bitrate_compliance
    from app.services.motion_recording_controller import refresh_camera_config

    await refresh_camera_config(str(cam["_id"]), cfg)
    # Cache compliance on runtime state (informational)
    try:
        from app.services import motion_recording_controller as ctl

        br = await evaluate_motion_bitrate_compliance(
            str(cam["_id"]),
            idle_stream=cfg["idle_stream"],
            active_stream=cfg["active_stream"],
        )
        st = ctl._STATE.setdefault(str(cam["_id"]), {})
        st["bitrate_compliance"] = {
            "compliance": br.get("compliance"),
            "idle_bitrate_kbps": br.get("idle_bitrate_kbps"),
            "active_bitrate_kbps": br.get("active_bitrate_kbps"),
            "message": br.get("message"),
        }
    except Exception:
        pass
    return await get_motion_recording_settings(camera_id)


async def list_motion_enabled_cameras() -> list[dict]:
    out: list[dict] = []
    cursor = camera_collection.find({"motion_recording.enabled": True})
    async for cam in cursor:
        out.append(cam)
    return out
