"""Cascade updates when camera fields change (RTSP URLs, go2rtc, recording)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from app.services.camera_uid import camera_display_name, make_camera_uid
from app.services.rtsp_utils import rtsp_url_credentials_stale, sync_camera_rtsp_urls

logger = logging.getLogger(__name__)

# Fields that change live/recording stream endpoints.
STREAM_AFFECTING_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "username",
        "ip_address",
        "port",
        "protocol",
        "model",
        "main_channel",
        "sub_channel",
        "preview_channel",
        "recording_channel",
        "main_rtsp_url",
        "sub_rtsp_url",
        "preview_rtsp_url",
        "rtsp_url",
        "is_active",
    }
)

RTSP_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "main_rtsp_url",
        "sub_rtsp_url",
        "preview_rtsp_url",
        "rtsp_url",
        "rtsp_url_source",
        "main_channel",
        "sub_channel",
        "recording_channel",
        "preview_channel",
    }
)


def changed_fields(existing: Optional[dict], updated: dict) -> Set[str]:
    """Keys in updated whose values differ from existing."""
    if not existing:
        return set(updated.keys())
    out: Set[str] = set()
    for key, value in updated.items():
        if key == "_id":
            continue
        if existing.get(key) != value:
            out.add(key)
    return out


def stream_config_changed(existing: Optional[dict], fields: dict) -> bool:
    """True when live/recording endpoints need to be refreshed."""
    if not existing:
        return True
    if changed_fields(existing, fields) & STREAM_AFFECTING_FIELDS:
        return True
    merged = {**existing, **fields}
    return rtsp_url_credentials_stale(merged)


def finalize_camera_fields(existing: Optional[dict], fields: dict) -> dict:
    """Rebuild derived RTSP URLs, camera_uid, and display_name from current settings."""
    return finalize_camera_document({**(existing or {}), **fields}, existing=existing)


def finalize_camera_document(doc: dict, *, existing: Optional[dict] = None) -> dict:
    """Return camera doc with derived RTSP URLs, camera_uid, and display_name synced."""
    out = dict(doc)
    merged = {**(existing or {}), **out}
    ip = (out.get("ip_address") or merged.get("ip_address") or "").strip()
    if ip:
        out["camera_uid"] = make_camera_uid(ip) or ""

    synced = sync_camera_rtsp_urls({**merged, **out})
    for key in RTSP_DERIVED_FIELDS:
        if key in synced:
            out[key] = synced[key]

    out["display_name"] = camera_display_name({**merged, **out})
    return out


async def apply_camera_side_effects(
    camera_id: str,
    *,
    existing: Optional[dict] = None,
    updated_fields: Optional[dict] = None,
    reason: str = "camera_change",
) -> Dict[str, Any]:
    """Reload go2rtc (and recording when needed) after stream-affecting changes."""
    result: Dict[str, Any] = {"ok": True, "reason": reason, "go2rtc": None, "recording": None}

    if updated_fields is not None and not stream_config_changed(existing, updated_fields):
        result["skipped"] = True
        return result

    from app.services.go2rtc_service import GO2RTC_ENABLED, LIVE_PROVIDER, ensure_go2rtc_streams

    if GO2RTC_ENABLED and LIVE_PROVIDER == "go2rtc":
        try:
            sync = await ensure_go2rtc_streams()
            result["go2rtc"] = {"ok": bool(sync.get("ok")), "streams": sync.get("streamCount")}
        except Exception as exc:
            logger.warning("[camera_sync] go2rtc refresh failed: %s", exc)
            result["go2rtc"] = {"ok": False, "error": str(exc)}

    rec = await _maybe_restart_recording(camera_id, existing)
    if rec:
        result["recording"] = rec

    return result


async def _maybe_restart_recording(camera_id: str, existing: Optional[dict]) -> Optional[dict]:
    """Restart active recording so ffmpeg picks up new RTSP URLs."""
    if not existing:
        return None
    cid = str(camera_id)
    try:
        from app.services.recording_schedule_store import recording_schedule
        from app.services.video_recording import (
            is_camera_recording,
            start_camera_recording,
            stop_camera_recording,
        )
    except ImportError:
        return None

    if not recording_schedule.get(cid, False):
        return None
    if not await is_camera_recording(cid):
        return None

    try:
        await stop_camera_recording(cid)
        await start_camera_recording(cid)
        logger.info("[camera_sync] Restarted recording for camera %s after stream change", cid)
        return {"restarted": True, "camera_id": cid}
    except Exception as exc:
        logger.warning("[camera_sync] Recording restart failed for %s: %s", cid, exc)
        return {"restarted": False, "camera_id": cid, "error": str(exc)}


def schedule_camera_side_effects(
    camera_id: str,
    *,
    existing: Optional[dict] = None,
    updated_fields: Optional[dict] = None,
    reason: str = "camera_change",
) -> None:
    """Fire-and-forget cascade refresh (go2rtc, recording)."""

    async def _run() -> None:
        try:
            await apply_camera_side_effects(
                camera_id,
                existing=existing,
                updated_fields=updated_fields,
                reason=reason,
            )
        except Exception as exc:
            logger.warning("[camera_sync] Side effects failed (%s): %s", reason, exc)

    asyncio.create_task(_run())


async def apply_bulk_camera_side_effects(
    *,
    any_stream_change: bool = True,
    reason: str = "bulk_camera_change",
) -> Dict[str, Any]:
    """Single go2rtc reload after import/bulk updates."""
    if not any_stream_change:
        return {"ok": True, "skipped": True, "reason": reason}
    return await apply_camera_side_effects("", existing=None, updated_fields=None, reason=reason)
