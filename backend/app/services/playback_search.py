"""Playback search — find recording sessions/segments by camera and date."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection, recording_sessions_collection
from app.services.recording_config import RECORDING_SEGMENT_SECONDS
from app.services.camera_identity import (
    camera_display_name,
    get_camera_by_ref,
    recording_session_mongo_filter,
    resolve_camera_uid,
    storage_folder_keys_for_uid,
)
from app.services.video_recording import (
    ACTIVE_RECORDINGS,
    RECORDINGS_DIR,
    _session_stats,
    session_storage_dir,
)

logger = logging.getLogger(__name__)

RECORDING_FILE_NOT_FOUND = "Recording file not found"


def _parse_date(date_str: str) -> tuple[datetime, datetime]:
    """UTC bounds [start, end) for a calendar date YYYY-MM-DD."""
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day_start, day_start + timedelta(days=1)


def _parse_iso(iso: str | None) -> Optional[datetime]:
    if not iso:
        return None
    try:
        text = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _list_segments(session_dir: Path) -> list[Path]:
    if not session_dir.is_dir():
        return []
    segments = sorted(session_dir.glob("seg_*.ts"))
    if not segments:
        segments = sorted(session_dir.glob("*.ts"))
    return segments


def _has_playable_media(session_dir: Path) -> bool:
    """True only when the session has a playlist and at least one non-empty segment."""
    if not session_dir.is_dir():
        return False
    if not (session_dir / "index.m3u8").is_file():
        return False
    segments = _list_segments(session_dir)
    return any(s.is_file() and s.stat().st_size > 0 for s in segments)


def _segment_bounds(session_dir: Path) -> tuple[Optional[datetime], Optional[datetime], int]:
    segments = _list_segments(session_dir)
    if not segments:
        return None, None, 0
    mtimes = [
        datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) for f in segments
    ]
    return min(mtimes), max(mtimes), len(segments)


def _has_segment_on_day(session_dir: Path, day_start: datetime, day_end: datetime) -> bool:
    for segment in _list_segments(session_dir):
        mtime = datetime.fromtimestamp(segment.stat().st_mtime, tz=timezone.utc)
        if day_start <= mtime < day_end:
            return True
    return False


def _interval_overlaps_day(
    start: Optional[datetime],
    end: Optional[datetime],
    day_start: datetime,
    day_end: datetime,
) -> bool:
    if start is None and end is None:
        return False
    if start is None:
        start = end
    if end is None:
        end = start
    if start is None or end is None:
        return False
    return start < day_end and end >= day_start


def _clip_to_day(
    start: datetime,
    end: datetime,
    day_start: datetime,
    day_end: datetime,
) -> tuple[datetime, datetime]:
    return max(start, day_start), min(end, day_end)


def _playlist_url(camera_id: str, session_id: str) -> str:
    return f"/api/playback/{camera_id}/{session_id}/media/index.m3u8"


def _duration_seconds(start: datetime, end: datetime, segment_count: int) -> int:
    seconds = max(0, int((end - start).total_seconds()))
    if seconds > 0:
        return seconds
    if segment_count > 0:
        return segment_count * int(float(RECORDING_SEGMENT_SECONDS))
    return 0


async def _resolve_camera_name(camera_ref: str) -> Optional[str]:
    from app.services.camera_identity import LEGACY_UNMAPPED_NAME

    if camera_ref == "legacy_unmapped":
        return LEGACY_UNMAPPED_NAME
    doc = await get_camera_by_ref(camera_ref)
    if doc:
        return camera_display_name(doc)
    return None


def _session_status(session_id: str, doc_status: str | None) -> str:
    for entry in ACTIVE_RECORDINGS.values():
        if entry.get("session_id") == session_id:
            return "recording"
    return doc_status or "unknown"


def _session_folder_candidates(
    doc: dict | None,
    storage_folders: list[str],
) -> list[str]:
    """Folder keys to check for on-disk footage (storage_path, uid, legacy ids)."""
    candidates: list[str] = []
    if doc:
        path = (doc.get("storage_path") or "").strip()
        if path and "/" in path:
            candidates.append(path.split("/", 1)[0])
        for key in ("camera_uid", "camera_id"):
            value = (doc.get(key) or "").strip()
            if value:
                candidates.append(value)
    candidates.extend(storage_folders)
    seen: set[str] = set()
    out: list[str] = []
    for folder in candidates:
        folder = (folder or "").strip()
        if not folder or folder in seen:
            continue
        seen.add(folder)
        out.append(folder)
    return out


def _resolve_playback_session_dir(
    session_id: str,
    doc: dict | None,
    storage_folders: list[str],
) -> Path | None:
    """Return the session directory only when it contains playable media."""
    for folder_id in _session_folder_candidates(doc, storage_folders):
        session_dir = session_storage_dir(folder_id, session_id)
        if _has_playable_media(session_dir):
            return session_dir
    return None


def _build_recording_entry(
    camera_id: str,
    session_id: str,
    *,
    doc: dict | None,
    day_start: datetime,
    day_end: datetime,
    storage_folders: list[str] | None = None,
) -> dict | None:
    session_dir = _resolve_playback_session_dir(
        session_id,
        doc,
        storage_folders or [camera_id],
    )
    if session_dir is None:
        if doc is not None:
            logger.warning(
                "[PLAYBACK] Skipping orphan MongoDB session (files deleted): "
                "camera=%s session=%s date=%s",
                camera_id,
                session_id,
                day_start.date().isoformat(),
            )
        return None

    disk_first, disk_last, disk_seg_count = _segment_bounds(session_dir)

    started = _parse_iso(doc.get("started_at") if doc else None) or disk_first
    stopped = (
        _parse_iso(doc.get("stopped_at") if doc else None)
        or _parse_iso(doc.get("latest_segment_time") if doc else None)
        or disk_last
    )

    if session_dir.is_dir():
        stats = _session_stats(session_dir)
        if stats["segment_count"] > disk_seg_count:
            disk_seg_count = stats["segment_count"]
        if not stopped and stats.get("latest_segment_time"):
            stopped = _parse_iso(stats["latest_segment_time"])

    status = _session_status(session_id, (doc or {}).get("status"))
    metadata_source = "filesystem" if doc is None else "mongodb"

    overlaps = _interval_overlaps_day(started, stopped, day_start, day_end)
    if not overlaps and not _has_segment_on_day(session_dir, day_start, day_end):
        return None

    # Prefer on-disk segment times so the timeline matches actual footage.
    if disk_first and disk_last:
        clip_start, clip_end = _clip_to_day(disk_first, disk_last, day_start, day_end)
    elif started and stopped:
        clip_start, clip_end = _clip_to_day(started, stopped, day_start, day_end)
    else:
        clip_start = day_start
        clip_end = day_start

    folder_id = session_dir.parent.parent.name
    storage_path = (doc or {}).get("storage_path") or f"{folder_id}/sessions/{session_id}"

    if doc is None:
        logger.info(
            "[PLAYBACK] Filesystem fallback: session %s for camera %s (no MongoDB metadata)",
            session_id,
            folder_id,
        )

    return {
        "sessionId": session_id,
        "startTime": clip_start.isoformat(),
        "endTime": clip_end.isoformat(),
        "duration": _duration_seconds(clip_start, clip_end, disk_seg_count),
        "filePath": storage_path,
        "playlistUrl": _playlist_url(folder_id, session_id),
        "status": status,
        "segmentCount": disk_seg_count,
        "playable": True,
        "error": None,
        "metadataSource": metadata_source,
    }


def _session_folder_id(doc: dict | None, storage_id: str) -> str:
    if doc:
        path = doc.get("storage_path") or ""
        if path:
            return path.split("/", 1)[0]
        cid = doc.get("camera_id")
        if cid:
            return str(cid)
    return storage_id


async def search_recordings_by_date(camera_ref: str, date_str: str) -> dict:
    """Return sessions/segments for a camera on a given calendar date."""
    day_start, day_end = _parse_date(date_str)
    camera_name = await _resolve_camera_name(camera_ref)
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    storage_folders = await storage_folder_keys_for_uid(uid)
    has_disk = any((RECORDINGS_DIR / fid).is_dir() for fid in storage_folders)

    if camera_name is None and not has_disk:
        return {"error": "Camera not found", "status": 404}

    seen_ids: set[str] = set()
    recordings: list[dict] = []

    session_filter = await recording_session_mongo_filter(camera_ref)
    if "$or" in session_filter:
        mongo_query = {
            "$and": [session_filter, {"status": {"$nin": ["deleted"]}}],
        }
    else:
        mongo_query = {**session_filter, "status": {"$nin": ["deleted"]}}

    cursor = recording_sessions_collection.find(mongo_query).sort("started_at", 1)
    async for doc in cursor:
        session_id = str(doc["_id"])
        seen_ids.add(session_id)
        folder_id = _session_folder_id(doc, storage_folders[0])
        entry = _build_recording_entry(
            folder_id,
            session_id,
            doc=doc,
            day_start=day_start,
            day_end=day_end,
            storage_folders=storage_folders,
        )
        if entry:
            recordings.append(entry)

    filesystem_fallback_count = 0
    for folder_id in storage_folders:
        camera_dir = RECORDINGS_DIR / folder_id
        sessions_root = camera_dir / "sessions"
        if not sessions_root.is_dir():
            continue
        for session_dir in sorted(sessions_root.iterdir()):
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name
            if session_id in seen_ids:
                continue
            entry = _build_recording_entry(
                folder_id,
                session_id,
                doc=None,
                day_start=day_start,
                day_end=day_end,
            )
            if entry:
                if entry.get("metadataSource") == "filesystem":
                    filesystem_fallback_count += 1
                recordings.append(entry)

    if filesystem_fallback_count:
        logger.info(
            "[PLAYBACK] Filesystem scan added %d session(s) without MongoDB metadata "
            "for camera %s (folders %s) on %s",
            filesystem_fallback_count,
            camera_ref,
            storage_folders,
            date_str,
        )

    recordings.sort(key=lambda item: item["startTime"])

    return {
        "cameraId": camera_ref,
        "cameraUid": uid,
        "cameraName": camera_name or camera_ref,
        "date": date_str,
        "recordings": recordings,
        "total": len(recordings),
    }


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _session_interval(
    camera_id: str,
    session_id: str,
    doc: dict | None,
) -> tuple[Optional[datetime], Optional[datetime]]:
    session_dir = session_storage_dir(camera_id, session_id)
    disk_first, disk_last, _ = _segment_bounds(session_dir)
    started = _parse_iso(doc.get("started_at") if doc else None) or disk_first
    stopped = (
        _parse_iso(doc.get("stopped_at") if doc else None)
        or _parse_iso(doc.get("latest_segment_time") if doc else None)
        or disk_last
    )
    if session_dir.is_dir():
        stats = _session_stats(session_dir)
        if not stopped and stats.get("latest_segment_time"):
            stopped = _parse_iso(stats["latest_segment_time"])
    return started, stopped


def _dates_for_interval(
    start: Optional[datetime],
    end: Optional[datetime],
    month_start: datetime,
    month_end: datetime,
) -> set[str]:
    if start is None and end is None:
        return set()
    if start is None:
        start = end
    if end is None:
        end = start
    if start is None or end is None:
        return set()

    out: set[str] = set()
    day = month_start
    while day < month_end:
        day_end = day + timedelta(days=1)
        if _interval_overlaps_day(start, end, day, day_end):
            out.add(day.strftime("%Y-%m-%d"))
        day = day_end
    return out


async def get_recording_dates_for_month(camera_ref: str, year: int, month: int) -> dict:
    """Return calendar dates (YYYY-MM-DD) that have recordings for a camera in a month."""
    if month < 1 or month > 12:
        return {"error": "month must be 1-12", "status": 400}

    camera_name = await _resolve_camera_name(camera_ref)
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    storage_folders = await storage_folder_keys_for_uid(uid)
    has_disk = any((RECORDINGS_DIR / fid).is_dir() for fid in storage_folders)
    if camera_name is None and not has_disk:
        return {"error": "Camera not found", "status": 404}

    month_start, month_end = _month_bounds(year, month)
    dates: set[str] = set()
    seen_ids: set[str] = set()

    session_filter = await recording_session_mongo_filter(camera_ref)
    if "$or" in session_filter:
        mongo_query = {
            "$and": [session_filter, {"status": {"$nin": ["deleted"]}}],
        }
    else:
        mongo_query = {**session_filter, "status": {"$nin": ["deleted"]}}

    cursor = recording_sessions_collection.find(mongo_query)
    async for doc in cursor:
        session_id = str(doc["_id"])
        seen_ids.add(session_id)
        session_dir = _resolve_playback_session_dir(
            session_id,
            doc,
            storage_folders,
        )
        if session_dir is None:
            continue
        folder_id = session_dir.parent.parent.name
        started, stopped = _session_interval(folder_id, session_id, doc)
        dates |= _dates_for_interval(started, stopped, month_start, month_end)
        day = month_start
        while day < month_end:
            if _has_segment_on_day(session_dir, day, day + timedelta(days=1)):
                dates.add(day.strftime("%Y-%m-%d"))
            day += timedelta(days=1)

    for folder_id in storage_folders:
        camera_dir = RECORDINGS_DIR / folder_id
        sessions_root = camera_dir / "sessions"
        if not sessions_root.is_dir():
            continue
        for session_dir in sessions_root.iterdir():
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name
            if session_id in seen_ids:
                continue
            if _has_playable_media(session_dir):
                started, stopped = _session_interval(folder_id, session_id, None)
                dates |= _dates_for_interval(started, stopped, month_start, month_end)
            day = month_start
            while day < month_end:
                if _has_segment_on_day(session_dir, day, day + timedelta(days=1)):
                    dates.add(day.strftime("%Y-%m-%d"))
                day += timedelta(days=1)

    return {
        "cameraId": camera_ref,
        "cameraUid": uid,
        "cameraName": camera_name or camera_ref,
        "year": year,
        "month": month,
        "dates": sorted(dates),
    }
