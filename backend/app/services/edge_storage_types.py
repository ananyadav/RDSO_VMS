"""RDSO 18.3.16 — edge storage failover / backfill types and helpers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_PARTIAL = "partial"
JOB_FAILED = "failed"

JOB_STATUSES = frozenset(
    {JOB_PENDING, JOB_RUNNING, JOB_COMPLETED, JOB_PARTIAL, JOB_FAILED}
)

PROTOCOL_ISAPI = "hikvision_isapi"
PROTOCOL_ONVIF_G = "onvif_profile_g"
PROTOCOL_NONE = "unsupported"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(iso: str | None) -> Optional[datetime]:
    if not iso:
        return None
    try:
        text = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def job_key(camera_id: str, start: datetime, end: datetime) -> str:
    raw = f"{camera_id}|{to_iso(start)}|{to_iso(end)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def normalize_interval(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    a = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    b = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    a = a.astimezone(timezone.utc)
    b = b.astimezone(timezone.utc)
    if b < a:
        a, b = b, a
    return a, b


def clip_overlap(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> Optional[tuple[datetime, datetime]]:
    start = max(a0, b0)
    end = min(a1, b1)
    if end <= start:
        return None
    return start, end


def public_job(doc: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": str(doc.get("_id") or doc.get("id") or ""),
        "camera_id": doc.get("camera_id"),
        "camera_uid": doc.get("camera_uid") or "",
        "job_key": doc.get("job_key"),
        "status": doc.get("status"),
        "gap_start": doc.get("gap_start"),
        "gap_end": doc.get("gap_end"),
        "protocol": doc.get("protocol"),
        "edge_supported": doc.get("edge_supported"),
        "edge_clips_found": doc.get("edge_clips_found", 0),
        "bytes_downloaded": doc.get("bytes_downloaded", 0),
        "session_ids": list(doc.get("session_ids") or []),
        "message": doc.get("message") or "",
        "error": doc.get("error"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "idempotent_reuse": bool(doc.get("idempotent_reuse")),
    }
