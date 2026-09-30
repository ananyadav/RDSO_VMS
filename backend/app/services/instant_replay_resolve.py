"""Resolve Instant Replay targets from permanent recording sessions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.database import recording_sessions_collection
from app.services.camera_identity import (
    camera_display_name,
    get_camera_by_ref,
    recording_session_mongo_filter,
    resolve_camera_uid,
    storage_folder_keys_for_uid,
)
from app.services.playback_search import (
    _has_playable_media,
    _parse_iso,
    _resolve_playback_session_dir,
    _session_interval,
)
from app.services.storage_settings_store import get_effective_recordings_dir
from app.services.video_recording import ACTIVE_RECORDINGS

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _playlist_url(folder_id: str, session_id: str) -> str:
    return f"/api/playback/{folder_id}/{session_id}/media/index.m3u8"


def _session_status(session_id: str, doc_status: str | None) -> str:
    for entry in ACTIVE_RECORDINGS.values():
        if entry.get("session_id") == session_id:
            return "recording"
    return doc_status or "unknown"


async def _iter_playable_sessions(camera_ref: str) -> list[dict[str, Any]]:
    """Newest-first list of playable permanent sessions for a camera."""
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    storage_folders = await storage_folder_keys_for_uid(uid)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    session_filter = await recording_session_mongo_filter(camera_ref)
    if "$or" in session_filter:
        mongo_query = {"$and": [session_filter, {"status": {"$nin": ["deleted"]}}]}
    else:
        mongo_query = {**session_filter, "status": {"$nin": ["deleted"]}}

    cursor = recording_sessions_collection.find(mongo_query).sort("started_at", -1)
    async for doc in cursor:
        session_id = str(doc["_id"])
        seen.add(session_id)
        session_dir = _resolve_playback_session_dir(session_id, doc, storage_folders)
        if session_dir is None:
            continue
        folder_id = session_dir.parent.parent.name
        started, stopped = _session_interval(folder_id, session_id, doc)
        if started is None:
            continue
        if stopped is None:
            stopped = _utc_now() if _session_status(session_id, doc.get("status")) == "recording" else started
        out.append(
            {
                "sessionId": session_id,
                "folderId": folder_id,
                "started": started,
                "stopped": stopped,
                "status": _session_status(session_id, doc.get("status")),
                "sourceType": "recording",
                "sessionDir": session_dir,
                "doc": doc,
            }
        )

    for folder_id in storage_folders:
        sessions_root = get_effective_recordings_dir() / folder_id / "sessions"
        if not sessions_root.is_dir():
            continue
        for session_dir in sessions_root.iterdir():
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name
            if session_id in seen:
                continue
            if not _has_playable_media(session_dir):
                continue
            started, stopped = _session_interval(folder_id, session_id, None)
            if started is None:
                continue
            if stopped is None:
                stopped = started
            out.append(
                {
                    "sessionId": session_id,
                    "folderId": folder_id,
                    "started": started,
                    "stopped": stopped,
                    "status": "filesystem",
                    "sourceType": "recording",
                    "sessionDir": session_dir,
                    "doc": None,
                }
            )

    out.sort(key=lambda s: s["started"], reverse=True)
    return out


def _window_from_sessions(sessions: list[dict[str, Any]]) -> tuple[Optional[datetime], Optional[datetime]]:
    if not sessions:
        return None, None
    oldest = min(s["started"] for s in sessions)
    newest = max(s["stopped"] for s in sessions)
    return oldest, newest


def _find_covering_session(
    sessions: list[dict[str, Any]],
    target: datetime,
) -> Optional[dict[str, Any]]:
    """Half-open [started, stopped) — active sessions use stopped=now."""
    for sess in sessions:
        start = sess["started"]
        end = sess["stopped"]
        if start <= target < end or (
            sess.get("status") == "recording" and start <= target <= end
        ):
            return sess
        # Inclusive end for just-stopped edge
        if start <= target <= end:
            return sess
    return None


def _offset_seconds(session: dict[str, Any], target: datetime) -> float:
    start = session["started"]
    end = session["stopped"]
    raw = (target - start).total_seconds()
    duration = max(0.0, (end - start).total_seconds())
    if duration <= 0:
        return 0.0
    return max(0.0, min(duration, raw))


async def resolve_instant_replay_from_recordings(
    camera_ref: str,
    *,
    seconds_ago: Optional[float] = None,
    at_iso: Optional[str] = None,
) -> dict[str, Any]:
    """
    Resolve a recent timestamp against permanent recording sessions.

    Returns either a playable payload or {"ok": False, "error": ..., "code": "no_footage"|"bad_request"}.
    """
    cam = await get_camera_by_ref(camera_ref)
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    camera_id = str(cam["_id"]) if cam else camera_ref
    camera_name = camera_display_name(cam) if cam else camera_ref

    now = _utc_now()
    if at_iso:
        target = _parse_iso(at_iso)
        if target is None:
            return {"ok": False, "error": "Invalid at timestamp", "code": "bad_request"}
    elif seconds_ago is not None:
        try:
            secs = float(seconds_ago)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid secondsAgo", "code": "bad_request"}
        if secs < 0:
            return {"ok": False, "error": "secondsAgo must be >= 0", "code": "bad_request"}
        target = now - timedelta(seconds=secs)
    else:
        # Default: 30 seconds ago
        target = now - timedelta(seconds=30)

    sessions = await _iter_playable_sessions(camera_ref)
    oldest, newest = _window_from_sessions(sessions)

    base = {
        "ok": True,
        "cameraId": camera_id,
        "cameraUid": uid,
        "cameraName": camera_name,
        "requestedAt": target.isoformat(),
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "sourceType": None,
        "sessionId": None,
        "playlistUrl": None,
        "offsetSeconds": None,
        "status": None,
    }

    if not sessions or oldest is None or newest is None:
        return {
            **base,
            "ok": False,
            "error": "Recent footage is not available for the selected time",
            "code": "no_footage",
        }

    if target < oldest:
        return {
            **base,
            "ok": False,
            "error": "Recent footage is not available for the selected time",
            "code": "no_footage",
        }

    # Clamp slightly into the future to newest
    if target > newest:
        target = newest

    covering = _find_covering_session(sessions, target)
    if covering is None:
        return {
            **base,
            "ok": False,
            "error": "Recent footage is not available for the selected time",
            "code": "no_footage",
        }

    offset = _offset_seconds(covering, target)
    return {
        **base,
        "ok": True,
        "requestedAt": target.isoformat(),
        "sourceType": "recording",
        "sessionId": covering["sessionId"],
        "playlistUrl": _playlist_url(covering["folderId"], covering["sessionId"]),
        "offsetSeconds": round(offset, 3),
        "status": covering["status"],
        "sessionStartAt": covering["started"].isoformat(),
        "sessionEndAt": covering["stopped"].isoformat(),
    }


async def recording_availability_window(camera_ref: str) -> dict[str, Any]:
    sessions = await _iter_playable_sessions(camera_ref)
    oldest, newest = _window_from_sessions(sessions)
    return {
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "sessionCount": len(sessions),
        "hasActiveRecording": any(s.get("status") == "recording" for s in sessions),
    }


def _parse_avail(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    return _parse_iso(iso)


async def resolve_instant_replay(
    camera_ref: str,
    *,
    seconds_ago: Optional[float] = None,
    at_iso: Optional[str] = None,
) -> dict[str, Any]:
    """
    Resolve Instant Replay footage.

    Source selection (do NOT blindly prefer permanent recording):
      1. Short IR buffer when it covers the target AND permanent HLS is not
         reliable for that lookback (open long segment / very recent).
      2. Permanent recording when it covers the target AND lookback is old
         enough that completed archive segments are usable.
      3. IR buffer when only the buffer covers the target.
      4. Permanent recording only as a last resort when buffer is absent and
         permanent covers an older target.
      5. no_footage — never fabricate / never jump to wrong time.
    """
    from app.services.instant_replay_buffer import get_buffer_window, resolve_buffer_offset
    from app.services.instant_replay_config import (
        INSTANT_REPLAY_ENABLED,
        instant_replay_public_config,
        permanent_hls_reliable_for_lookback,
        permanent_recording_segment_seconds,
    )

    cam = await get_camera_by_ref(camera_ref)
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    camera_id = str(cam["_id"]) if cam else camera_ref
    camera_name = camera_display_name(cam) if cam else camera_ref
    cfg = instant_replay_public_config()

    now = _utc_now()
    if at_iso:
        target = _parse_iso(at_iso)
        if target is None:
            return {
                "ok": False,
                "error": "Invalid at timestamp",
                "code": "bad_request",
                "config": cfg,
            }
    elif seconds_ago is not None:
        try:
            secs = float(seconds_ago)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "Invalid secondsAgo",
                "code": "bad_request",
                "config": cfg,
            }
        if secs < 0:
            return {
                "ok": False,
                "error": "secondsAgo must be >= 0",
                "code": "bad_request",
                "config": cfg,
            }
        target = now - timedelta(seconds=secs)
    else:
        target = now - timedelta(seconds=30)

    lookback = max(0.0, (now - target).total_seconds())
    permanent_ok = permanent_hls_reliable_for_lookback(lookback)

    recording = await resolve_instant_replay_from_recordings(
        camera_ref, seconds_ago=None, at_iso=target.isoformat()
    )
    win = get_buffer_window(uid) if INSTANT_REPLAY_ENABLED else {
        "active": False,
        "oldestAvailableAt": None,
        "newestAvailableAt": None,
        "segmentCount": 0,
    }
    buf = resolve_buffer_offset(uid, target) if INSTANT_REPLAY_ENABLED else None

    rec_oldest = _parse_avail(recording.get("oldestAvailableAt"))
    rec_newest = _parse_avail(recording.get("newestAvailableAt"))
    buf_oldest = _parse_avail(win.get("oldestAvailableAt"))
    buf_newest = _parse_avail(win.get("newestAvailableAt"))

    candidates = [t for t in (rec_oldest, buf_oldest) if t is not None]
    candidates_new = [t for t in (rec_newest, buf_newest) if t is not None]
    oldest = min(candidates) if candidates else None
    newest = max(candidates_new) if candidates_new else None

    base = {
        "cameraId": camera_id,
        "cameraUid": uid,
        "cameraName": camera_name,
        "requestedAt": target.isoformat(),
        "resolvedAt": None,
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "lookbackSeconds": round(lookback, 3),
        "permanentSegmentSeconds": permanent_recording_segment_seconds(),
        "permanentReliable": permanent_ok,
        "config": cfg,
    }

    buf_ok = bool(buf and buf.get("ok"))
    rec_ok = bool(recording.get("ok"))

    def _from_buffer() -> dict[str, Any]:
        assert buf is not None
        # Actual media time ≈ oldest + offset
        resolved = target
        if buf.get("oldestAvailableAt") and buf.get("offsetSeconds") is not None:
            o = _parse_avail(buf.get("oldestAvailableAt"))
            if o is not None:
                resolved = o + timedelta(seconds=float(buf["offsetSeconds"]))
        return {
            **base,
            **buf,
            "ok": True,
            "resolvedAt": resolved.isoformat(),
            "cameraId": camera_id,
            "cameraUid": uid,
            "cameraName": camera_name,
            "requestedAt": target.isoformat(),
            "oldestAvailableAt": oldest.isoformat() if oldest else buf.get("oldestAvailableAt"),
            "newestAvailableAt": newest.isoformat() if newest else buf.get("newestAvailableAt"),
            "config": cfg,
        }

    def _from_recording() -> dict[str, Any]:
        resolved = target
        if recording.get("sessionStartAt") and recording.get("offsetSeconds") is not None:
            start = _parse_avail(recording.get("sessionStartAt"))
            if start is not None:
                resolved = start + timedelta(seconds=float(recording["offsetSeconds"]))
        out = {**base, **{k: v for k, v in recording.items() if k != "config"}}
        out.update(
            {
                "ok": True,
                "resolvedAt": resolved.isoformat(),
                "sourceType": "recording",
                "config": cfg,
                "requestedAt": target.isoformat(),
                "oldestAvailableAt": oldest.isoformat() if oldest else recording.get("oldestAvailableAt"),
                "newestAvailableAt": newest.isoformat() if newest else recording.get("newestAvailableAt"),
            }
        )
        return out

    # Prefer short IR buffer for recent lookbacks when permanent HLS is unreliable.
    if buf_ok and not permanent_ok:
        return _from_buffer()

    # Older lookbacks: permanent archive is appropriate when it covers.
    if rec_ok and permanent_ok:
        return _from_recording()

    # Only buffer covers (e.g. non-recording camera, or gap in archive).
    if buf_ok:
        return _from_buffer()

    # Permanent covers recent target but IR buffer not ready — do not serve
    # coarse permanent open-segment media as Instant Replay.
    logger.info(
        "[INSTANT-REPLAY] no_footage camera=%s requested=%s lookback=%.1fs "
        "permanent_reliable=%s buffer_active=%s recording_ok=%s",
        uid,
        target.isoformat(),
        lookback,
        permanent_ok,
        win.get("active"),
        rec_ok,
    )
    return {
        **base,
        "ok": False,
        "error": "Recent footage is not available for the selected time",
        "code": "no_footage",
        "sourceType": None,
        "sessionId": None,
        "playlistUrl": None,
        "offsetSeconds": None,
        "status": None,
    }


def _preset_is_available(
    seconds: int,
    *,
    now: datetime,
    buf_oldest: Optional[datetime],
    buf_newest: Optional[datetime],
    rec_oldest: Optional[datetime],
    rec_newest: Optional[datetime],
) -> bool:
    """Enable go-back preset only when a reliable source covers that lookback."""
    from app.services.instant_replay_config import permanent_hls_reliable_for_lookback

    target = now - timedelta(seconds=float(seconds))
    buf_covers = (
        buf_oldest is not None
        and buf_newest is not None
        and buf_oldest <= target <= buf_newest + timedelta(seconds=5)
    )
    rec_covers = (
        rec_oldest is not None
        and rec_newest is not None
        and rec_oldest <= target <= rec_newest + timedelta(seconds=5)
    )
    if not permanent_hls_reliable_for_lookback(float(seconds)):
        # Recent precision requires short IR buffer coverage.
        return buf_covers
    return buf_covers or rec_covers


async def instant_replay_availability(camera_ref: str) -> dict[str, Any]:
    """Merged availability window for Instant Replay UI (recording + buffer)."""
    from app.services.instant_replay_buffer import get_buffer_window
    from app.services.instant_replay_config import (
        INSTANT_REPLAY_PRESETS_SECONDS,
        instant_replay_public_config,
        permanent_recording_segment_seconds,
    )

    cam = await get_camera_by_ref(camera_ref)
    uid = await resolve_camera_uid(camera_ref) or camera_ref
    camera_id = str(cam["_id"]) if cam else camera_ref
    camera_name = camera_display_name(cam) if cam else camera_ref

    rec = await recording_availability_window(camera_ref)
    buf = get_buffer_window(uid)

    rec_oldest = _parse_avail(rec.get("oldestAvailableAt"))
    rec_newest = _parse_avail(rec.get("newestAvailableAt"))
    buf_oldest = _parse_avail(buf.get("oldestAvailableAt"))
    buf_newest = _parse_avail(buf.get("newestAvailableAt"))

    candidates = [t for t in (rec_oldest, buf_oldest) if t is not None]
    candidates_new = [t for t in (rec_newest, buf_newest) if t is not None]
    oldest = min(candidates) if candidates else None
    newest = max(candidates_new) if candidates_new else None

    now = _utc_now()
    available_seconds = 0.0
    if oldest and newest:
        available_seconds = max(0.0, (newest - oldest).total_seconds())

    # Recent presets require buffer; older may use permanent archive.
    presets = [
        s
        for s in INSTANT_REPLAY_PRESETS_SECONDS
        if _preset_is_available(
            s,
            now=now,
            buf_oldest=buf_oldest,
            buf_newest=buf_newest,
            rec_oldest=rec_oldest,
            rec_newest=rec_newest,
        )
    ]

    # Practical recent window = IR buffer duration when active.
    recent_available_seconds = 0.0
    if buf_oldest and buf_newest:
        recent_available_seconds = max(0.0, (buf_newest - buf_oldest).total_seconds())

    return {
        "ok": True,
        "cameraId": camera_id,
        "cameraUid": uid,
        "cameraName": camera_name,
        "oldestAvailableAt": oldest.isoformat() if oldest else None,
        "newestAvailableAt": newest.isoformat() if newest else None,
        "availableSeconds": round(available_seconds, 3),
        "recentAvailableSeconds": round(recent_available_seconds, 3),
        "presetsSeconds": presets,
        "hasActiveRecording": bool(rec.get("hasActiveRecording")),
        "bufferActive": bool(buf.get("active")),
        "bufferSegmentCount": int(buf.get("segmentCount") or 0),
        "permanentSegmentSeconds": permanent_recording_segment_seconds(),
        "config": instant_replay_public_config(),
        "now": now.isoformat(),
    }
