"""Fast camera CSV/JSON bulk import — single fetch, bulk_write, targeted go2rtc sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from pymongo import UpdateOne

from app.core.database import camera_collection
from app.services.camera_form import prepare_camera_fields
from app.services.camera_locations import default_location_for_camera
from app.services.camera_sync import finalize_camera_document
from app.services.camera_uid import make_camera_uid

logger = logging.getLogger(__name__)

_BULK_CHUNK = 500


async def _load_camera_indexes() -> Tuple[Dict[str, dict], Dict[str, dict]]:
    by_uid: Dict[str, dict] = {}
    by_ip: Dict[str, dict] = {}
    async for cam in camera_collection.find({}):
        uid = (cam.get("camera_uid") or "").strip()
        ip = (cam.get("ip_address") or cam.get("ip") or "").strip()
        if uid:
            by_uid[uid] = cam
        if ip:
            by_ip[ip] = cam
    return by_uid, by_ip


def _find_existing(by_uid: Dict[str, dict], by_ip: Dict[str, dict], uid: str, ip: str) -> Optional[dict]:
    return by_uid.get(uid) or by_ip.get(ip)


async def bulk_import_cameras(
    rows: List[dict],
    *,
    mark_missing_inactive: bool = True,
) -> Dict[str, Any]:
    """
    Upsert cameras by IP / camera_uid using bulk_write.
    Marks cameras not in the import batch inactive with one update_many.
    Returns counts, affected worker ids, and row errors.
    """
    from app.services.go2rtc_workers import WORKERS_ENABLED, ensure_camera_worker_assigned, normalize_worker_id

    by_uid, by_ip = await _load_camera_indexes()
    active_ips: Set[str] = set()
    worker_ids: Set[int] = set()
    ops: List[UpdateOne] = []
    created = 0
    updated = 0
    errors: List[str] = []

    for row in rows:
        if not isinstance(row, dict):
            errors.append("invalid row")
            continue
        ip = (row.get("ip_address") or row.get("ip") or "").strip()
        if not ip:
            errors.append("row missing ip_address")
            continue

        camera_uid = make_camera_uid(ip)
        if not camera_uid:
            errors.append(f"{ip}: invalid IP")
            continue

        active_ips.add(ip)
        name = (row.get("name") or "").strip()
        if name:
            row["name"] = name
        loc = default_location_for_camera(name or ip, row)
        row.update(loc)
        row["is_active"] = row.get("is_active", True) is not False

        existing = _find_existing(by_uid, by_ip, camera_uid, ip)
        try:
            fields = prepare_camera_fields(row, existing=existing)
            merged = {**(existing or {}), **row, **fields, "ip_address": ip}
            doc = finalize_camera_document(merged, existing=existing)

            if existing:
                for key in ("recording_storage_id", "registered_at"):
                    if existing.get(key) is not None:
                        doc[key] = existing[key]
                if WORKERS_ENABLED:
                    doc = await ensure_camera_worker_assigned(doc, existing=existing)
                updated += 1
            else:
                if "registered_at" not in doc:
                    doc["registered_at"] = datetime.now(timezone.utc).isoformat()
                if WORKERS_ENABLED:
                    doc = await ensure_camera_worker_assigned(doc, existing=None)
                created += 1
                by_uid[camera_uid] = doc
                by_ip[ip] = doc

            wid = normalize_worker_id(doc.get("worker_id"))
            if wid:
                worker_ids.add(wid)
            if existing:
                prev = normalize_worker_id(existing.get("worker_id"))
                if prev:
                    worker_ids.add(prev)

            set_doc = {k: v for k, v in doc.items() if k != "_id"}
            ops.append(
                UpdateOne(
                    {"camera_uid": camera_uid},
                    {
                        "$set": set_doc,
                        "$setOnInsert": {
                            "registered_at": doc.get("registered_at")
                            or datetime.now(timezone.utc).isoformat(),
                        },
                    },
                    upsert=True,
                )
            )
        except Exception as exc:
            errors.append(f"{ip}: {exc}")

    if ops:
        for i in range(0, len(ops), _BULK_CHUNK):
            chunk = ops[i : i + _BULK_CHUNK]
            await camera_collection.bulk_write(chunk, ordered=False)

    inactive = 0
    if mark_missing_inactive and active_ips:
        inactive, deactivated_workers = await _mark_missing_inactive(active_ips)
        worker_ids.update(deactivated_workers)

    return {
        "created": created,
        "updated": updated,
        "markedInactive": inactive,
        "errors": errors,
        "workerIds": sorted(worker_ids),
    }


async def _mark_missing_inactive(active_ips: Set[str]) -> Tuple[int, Set[int]]:
    """One update_many for missing IPs; return count and affected worker ids."""
    from app.services.go2rtc_workers import normalize_worker_id

    ip_list = sorted(active_ips)
    query = {
        "ip_address": {"$nin": ip_list},
        "is_active": {"$ne": False},
    }
    worker_ids: Set[int] = set()
    async for cam in camera_collection.find(query, {"worker_id": 1}):
        wid = normalize_worker_id(cam.get("worker_id"))
        if wid:
            worker_ids.add(wid)

    result = await camera_collection.update_many(query, {"$set": {"is_active": False}})
    return int(result.modified_count), worker_ids
