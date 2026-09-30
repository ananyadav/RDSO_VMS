"""Mongo persistence for edge backfill jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId

from app.core.database import database
from app.services.edge_storage_types import (
    JOB_PENDING,
    JOB_RUNNING,
    public_job,
    utc_now_iso,
)

edge_backfill_jobs_collection = database.get_collection("edge_backfill_jobs")


async def ensure_edge_backfill_indexes() -> None:
    await edge_backfill_jobs_collection.create_index("job_key", name="idx_edge_job_key")
    await edge_backfill_jobs_collection.create_index(
        [("camera_id", 1), ("created_at", -1)],
        name="idx_edge_camera_created",
    )
    await edge_backfill_jobs_collection.create_index("status", name="idx_edge_status")


async def find_active_job_by_key(job_key: str) -> Optional[dict]:
    doc = await edge_backfill_jobs_collection.find_one(
        {
            "job_key": job_key,
            "status": {"$in": [JOB_PENDING, JOB_RUNNING]},
        }
    )
    return doc


async def find_completed_job_by_key(job_key: str) -> Optional[dict]:
    doc = await edge_backfill_jobs_collection.find_one(
        {
            "job_key": job_key,
            "status": {"$in": ["completed", "partial"]},
        },
        sort=[("finished_at", -1)],
    )
    return doc


async def create_job(doc: dict[str, Any]) -> dict:
    now = utc_now_iso()
    payload = {
        **doc,
        "status": doc.get("status") or JOB_PENDING,
        "created_at": now,
        "updated_at": now,
        "session_ids": list(doc.get("session_ids") or []),
        "bytes_downloaded": int(doc.get("bytes_downloaded") or 0),
        "edge_clips_found": int(doc.get("edge_clips_found") or 0),
    }
    result = await edge_backfill_jobs_collection.insert_one(payload)
    created = await edge_backfill_jobs_collection.find_one({"_id": result.inserted_id})
    return public_job(created)  # type: ignore[return-value]


async def update_job(job_id: str, updates: dict[str, Any]) -> Optional[dict]:
    patch = {**updates, "updated_at": utc_now_iso()}
    await edge_backfill_jobs_collection.update_one(
        {"_id": ObjectId(job_id)}, {"$set": patch}
    )
    doc = await edge_backfill_jobs_collection.find_one({"_id": ObjectId(job_id)})
    return public_job(doc)


async def get_job(job_id: str) -> Optional[dict]:
    try:
        doc = await edge_backfill_jobs_collection.find_one({"_id": ObjectId(job_id)})
    except Exception:
        return None
    return public_job(doc)


async def list_jobs(
    *,
    camera_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    query: dict[str, Any] = {}
    if camera_id:
        query["camera_id"] = camera_id
    out: list[dict] = []
    cursor = (
        edge_backfill_jobs_collection.find(query)
        .sort("created_at", -1)
        .limit(max(1, min(int(limit), 200)))
    )
    async for doc in cursor:
        pub = public_job(doc)
        if pub:
            out.append(pub)
    return out
