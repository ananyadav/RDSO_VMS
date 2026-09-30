"""Detect VMS recording gaps for RDSO 18.3.16 edge backfill."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import recording_sessions_collection
from app.services.camera_identity import recording_session_mongo_filter
from app.services.edge_storage_types import normalize_interval, parse_iso, to_iso
from app.services.recording_export import compute_gaps


def _session_coverage(doc: dict) -> Optional[tuple[datetime, datetime]]:
    start = parse_iso(doc.get("started_at"))
    end = parse_iso(doc.get("stopped_at")) or parse_iso(doc.get("latest_segment_time"))
    if start is None:
        return None
    if end is None:
        # Active / incomplete — treat as covering until now (no gap while live).
        if (doc.get("status") or "") == "recording":
            end = datetime.now(timezone.utc)
        else:
            end = start
    if end < start:
        end = start
    return start, end


async def list_session_coverage(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
) -> list[dict[str, Any]]:
    """Return playable/known coverage intervals from VMS sessions (including edge ingest)."""
    range_start, range_end = normalize_interval(range_start, range_end)
    filt = await recording_session_mongo_filter(camera_ref)
    filt = {**filt, "status": {"$ne": "deleted"}}
    pieces: list[dict[str, Any]] = []
    cursor = recording_sessions_collection.find(filt)
    async for doc in cursor:
        cov = _session_coverage(doc)
        if not cov:
            continue
        a, b = cov
        if b < range_start or a > range_end:
            continue
        pieces.append(
            {
                "session_id": str(doc["_id"]),
                "source": doc.get("source") or "vms",
                "clipStart": to_iso(max(a, range_start)),
                "clipEnd": to_iso(min(b, range_end)),
                "status": doc.get("status"),
            }
        )
    return pieces


async def detect_recording_gaps(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
    *,
    min_gap_seconds: float = 5.0,
) -> dict[str, Any]:
    """Identify missing VMS coverage in [range_start, range_end]."""
    range_start, range_end = normalize_interval(range_start, range_end)
    pieces = await list_session_coverage(camera_ref, range_start, range_end)
    raw_gaps = compute_gaps(range_start, range_end, pieces)
    gaps: list[dict[str, Any]] = []
    for g in raw_gaps:
        a = parse_iso(g.get("start"))
        b = parse_iso(g.get("end"))
        if a is None or b is None:
            continue
        if (b - a).total_seconds() < float(min_gap_seconds):
            continue
        gaps.append(
            {
                "start": to_iso(a),
                "end": to_iso(b),
                "duration_seconds": round((b - a).total_seconds(), 1),
                "reason": g.get("reason") or "no_footage",
            }
        )
    return {
        "camera_id": camera_ref,
        "range_start": to_iso(range_start),
        "range_end": to_iso(range_end),
        "coverage": pieces,
        "gaps": gaps,
        "gap_count": len(gaps),
    }


def merge_edge_clips_into_gaps(
    gaps: list[dict[str, Any]],
    edge_clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate each VMS gap with overlapping edge clip windows (no fabrication)."""
    out: list[dict[str, Any]] = []
    for g in gaps:
        gs = parse_iso(g["start"])
        ge = parse_iso(g["end"])
        if gs is None or ge is None:
            continue
        matches: list[dict[str, Any]] = []
        for clip in edge_clips:
            cs = parse_iso(clip.get("start") or clip.get("startTime"))
            ce = parse_iso(clip.get("end") or clip.get("endTime"))
            if cs is None or ce is None:
                continue
            start = max(gs, cs)
            end = min(ge, ce)
            if end <= start:
                continue
            matches.append(
                {
                    **clip,
                    "overlap_start": to_iso(start),
                    "overlap_end": to_iso(end),
                    "overlap_seconds": round((end - start).total_seconds(), 1),
                }
            )
        out.append({**g, "edge_clips": matches, "edge_clip_count": len(matches)})
    return out
