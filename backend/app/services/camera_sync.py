"""Cascade updates when camera fields change (RTSP URLs, go2rtc, recording)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from app.services.camera_uid import apply_default_camera_names, make_camera_uid
from app.services.rtsp_utils import rtsp_url_credentials_stale, sync_camera_rtsp_urls

logger = logging.getLogger(__name__)

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
        "recording_channel",
        "main_rtsp_url",
        "sub_rtsp_url",
        "is_active",
    }
)

RTSP_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "main_rtsp_url",
        "sub_rtsp_url",
        "rtsp_url_source",
        "main_channel",
        "sub_channel",
        "recording_channel",
    }
)


def changed_fields(existing: Optional[dict], updated: dict) -> Set[str]:
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
    if not existing:
        return True
    if changed_fields(existing, fields) & STREAM_AFFECTING_FIELDS:
        return True
    merged = {**existing, **fields}
    return rtsp_url_credentials_stale(merged)


def finalize_camera_fields(existing: Optional[dict], fields: dict) -> dict:
    return finalize_camera_document({**(existing or {}), **fields}, existing=existing)


def finalize_camera_document(doc: dict, *, existing: Optional[dict] = None) -> dict:
    out = dict(doc)
    merged = {**(existing or {}), **out}
    ip = (out.get("ip_address") or merged.get("ip_address") or "").strip()
    if ip:
        out["camera_uid"] = make_camera_uid(ip) or ""

    synced = sync_camera_rtsp_urls({**merged, **out})
    for key in RTSP_DERIVED_FIELDS:
        if key in synced:
            out[key] = synced[key]
    if "rtsp_fallback_urls" in synced:
        out["rtsp_fallback_urls"] = synced["rtsp_fallback_urls"]

    return apply_default_camera_names(out, existing=existing)


async def apply_camera_side_effects(
    camera_id: str,
    *,
    existing: Optional[dict] = None,
    updated_fields: Optional[dict] = None,
    reason: str = "camera_change",
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": True, "reason": reason, "go2rtc": None, "recording": None}

    if updated_fields is not None and not stream_config_changed(existing, updated_fields):
        result["skipped"] = True
        return result

    from app.services.go2rtc_service import GO2RTC_ENABLED
    from app.core.database import camera_collection
    from bson import ObjectId
    from bson.errors import InvalidId

    if GO2RTC_ENABLED:
        try:
            from app.services.go2rtc_workers import (
                WORKERS_ENABLED,
                get_worker_id_for_camera_doc,
                rebalance_if_needed,
                sync_all_workers,
                sync_worker,
            )

            if WORKERS_ENABLED:
                rebalance = await rebalance_if_needed(reason=reason)
                if rebalance.get("rebalanced"):
                    sync = await sync_all_workers()
                    result["go2rtc"] = {
                        "ok": bool(sync.get("ok")),
                        "rebalanced": True,
                        "workers": sync.get("workerCount"),
                        "rebalance": rebalance,
                    }
                elif camera_id:
                    try:
                        oid = ObjectId(camera_id)
                        cam = await camera_collection.find_one({"_id": oid})
                    except (InvalidId, TypeError):
                        cam = None
                    wid = await get_worker_id_for_camera_doc(cam)
                    sync = await sync_worker(wid)
                    result["go2rtc"] = {
                        "ok": bool(sync.get("ok")),
                        "workerId": wid,
                        "streams": sync.get("streamCount"),
                        "rebalance": rebalance,
                    }
                else:
                    sync = await sync_all_workers()
                    result["go2rtc"] = {
                        "ok": bool(sync.get("ok")),
                        "workers": sync.get("workerCount"),
                        "rebalance": rebalance,
                    }
            else:
                from app.services.go2rtc_service import ensure_go2rtc_streams

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
    worker_ids: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    if not any_stream_change:
        return {"ok": True, "skipped": True, "reason": reason}

    rebalance = None
    try:
        from app.services.go2rtc_workers import WORKERS_ENABLED, rebalance_if_needed

        if WORKERS_ENABLED:
            rebalance = await rebalance_if_needed(reason=reason)
    except Exception as exc:
        logger.warning("[camera_sync] worker rebalance failed: %s", exc)
        rebalance = {"ok": False, "error": str(exc)}

    if rebalance and rebalance.get("rebalanced"):
        result = await apply_camera_side_effects("", existing=None, updated_fields=None, reason=reason)
    elif worker_ids:
        result = await _sync_workers_only(worker_ids, reason=reason, rebalance=rebalance)
    else:
        result = await apply_camera_side_effects("", existing=None, updated_fields=None, reason=reason)

    if rebalance is not None:
        result["rebalance"] = rebalance
    return result


async def _sync_workers_only(
    worker_ids: Set[int],
    *,
    reason: str,
    rebalance: Optional[dict] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": True, "reason": reason, "go2rtc": None, "recording": None}
    try:
        from app.services.go2rtc_workers import WORKERS_ENABLED, sync_worker

        if not WORKERS_ENABLED:
            from app.services.go2rtc_service import ensure_go2rtc_streams

            sync = await ensure_go2rtc_streams()
            result["go2rtc"] = {"ok": bool(sync.get("ok")), "streams": sync.get("streamCount")}
            return result

        sync_results = []
        for wid in sorted(worker_ids):
            sync_results.append(await sync_worker(wid))
        result["go2rtc"] = {
            "ok": all(r.get("ok") for r in sync_results),
            "workers": sync_results,
            "rebalance": rebalance,
        }
    except Exception as exc:
        logger.warning("[camera_sync] targeted go2rtc sync failed: %s", exc)
        result["go2rtc"] = {"ok": False, "error": str(exc)}
    return result
