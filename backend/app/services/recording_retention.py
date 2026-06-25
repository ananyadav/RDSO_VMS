"""Auto-delete recordings older than RECORDING_RETENTION_HOURS / DAYS."""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.database import list_recording_sessions, update_recording_session
from app.services.storage_settings_store import get_effective_retention_seconds
from app.services.recording_config import get_retention_policy
from app.services.storage_settings_store import get_effective_recordings_dir
from app.services.video_recording import (
    session_storage_dir,
    is_camera_recording,
    sync_session_stats_to_db,
    _session_stats,
)

logger = logging.getLogger(__name__)

_last_pass_result: Optional[dict] = None


def get_last_retention_pass() -> Optional[dict]:
    return _last_pass_result


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _segment_files(session_dir: Path) -> list[Path]:
    segs = list(session_dir.glob("seg_*.ts"))
    return segs if segs else list(session_dir.glob("*.ts"))


def _prune_old_segments(session_dir: Path, cutoff_ts: float) -> tuple[int, int]:
    """Delete .ts files with mtime older than cutoff. Returns (count, bytes_freed)."""
    removed = 0
    freed = 0
    for f in _segment_files(session_dir):
        try:
            if f.stat().st_mtime < cutoff_ts:
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
                removed += 1
        except OSError as e:
            logger.debug(f"[RETENTION] skip {f}: {e}")
    return removed, freed


def _session_dir_empty(session_dir: Path) -> bool:
    return len(_segment_files(session_dir)) == 0


def _latest_segment_mtime(session_dir: Path) -> Optional[float]:
    segments = _segment_files(session_dir)
    if not segments:
        return None
    return max(f.stat().st_mtime for f in segments)


async def _mark_session_deleted(session_id: str, camera_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await update_recording_session(
        session_id,
        {
            "status": "deleted",
            "stopped_at": now,
            "deleted_at": now,
            **_session_stats(session_storage_dir(camera_id, session_id)),
        },
    )


async def _delete_session_folder(camera_id: str, session_id: str) -> int:
    folder = session_storage_dir(camera_id, session_id)
    if not folder.exists():
        return 0
    try:
        size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        shutil.rmtree(folder, ignore_errors=True)
        return size
    except OSError as e:
        logger.warning(f"[RETENTION] Could not delete {folder}: {e}")
        return 0


async def _process_session_folder(
    camera_id: str,
    session_id: str,
    cutoff_ts: float,
    *,
    is_live: bool,
) -> dict:
    """Prune or delete one session directory. Returns partial stats."""
    session_dir = session_storage_dir(camera_id, session_id)
    if not session_dir.is_dir():
        return {"pruned": 0, "freed": 0, "deleted": False}

    pruned, freed = _prune_old_segments(session_dir, cutoff_ts)

    if _session_dir_empty(session_dir):
        if is_live:
            # Active FFmpeg session — keep folder; stats sync only
            await sync_session_stats_to_db(camera_id, session_id)
            return {"pruned": pruned, "freed": freed, "deleted": False}

        extra_freed = await _delete_session_folder(camera_id, session_id)
        await _mark_session_deleted(session_id, camera_id)
        return {
            "pruned": pruned,
            "freed": freed + extra_freed,
            "deleted": True,
        }

    latest = _latest_segment_mtime(session_dir)
    if not is_live and latest is not None and latest < cutoff_ts:
        extra_freed = await _delete_session_folder(camera_id, session_id)
        await _mark_session_deleted(session_id, camera_id)
        return {
            "pruned": pruned,
            "freed": freed + extra_freed,
            "deleted": True,
        }

    if pruned:
        await sync_session_stats_to_db(camera_id, session_id)
    return {"pruned": pruned, "freed": freed, "deleted": False}


async def _scan_filesystem_sessions(cutoff_ts: float) -> dict:
    """Walk Recordings/{camera}/sessions/{id} and enforce retention."""
    totals = {"pruned_segments": 0, "freed_bytes": 0, "deleted_sessions": 0}

    rec_dir = get_effective_recordings_dir()
    if not rec_dir.is_dir():
        return totals

    live_sessions = set()
    from app.services.video_recording import ACTIVE_RECORDINGS

    for entry in ACTIVE_RECORDINGS.values():
        live_sessions.add(entry["session_id"])

    for camera_dir in rec_dir.iterdir():
        if not camera_dir.is_dir():
            continue
        camera_id = camera_dir.name
        sessions_root = camera_dir / "sessions"
        if not sessions_root.is_dir():
            continue

        for session_dir in sessions_root.iterdir():
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name
            is_live = session_id in live_sessions and await is_camera_recording(camera_id)

            result = await _process_session_folder(
                camera_id, session_id, cutoff_ts, is_live=is_live
            )
            totals["pruned_segments"] += result["pruned"]
            totals["freed_bytes"] += result["freed"]
            if result["deleted"]:
                totals["deleted_sessions"] += 1

    return totals


async def _cleanup_stale_mongo_sessions(cutoff_ts: float) -> int:
    """Remove DB rows for stopped sessions past retention with no recent segments."""
    deleted = 0
    sessions = await list_recording_sessions(camera_id=None, limit=500)
    for sess in sessions:
        if sess.get("status") in ("deleted",):
            continue
        sid = sess.get("id")
        cid = sess.get("camera_id")
        if not sid or not cid:
            storage_path = sess.get("storage_path") or sess.get("file_path") or ""
            if storage_path and not cid:
                cid = storage_path.split("/", 1)[0]
        if not sid or not cid:
            continue
        session_dir = session_storage_dir(cid, sid)

        if await is_camera_recording(cid):
            continue

        if session_dir.is_dir():
            continue  # handled by filesystem scan

        started = sess.get("started_at")
        if not started:
            continue
        if _parse_iso(started).timestamp() >= cutoff_ts:
            continue

        await _mark_session_deleted(sid, cid)
        deleted += 1

    return deleted


async def run_retention_pass() -> dict:
    """
    Delete recording segments and sessions older than the configured retention window.
    Uses segment file mtime as the primary age signal.
    """
    global _last_pass_result

    now = datetime.now(timezone.utc)
    cutoff_ts = now.timestamp() - get_effective_retention_seconds()
    policy = get_retention_policy()

    fs = await _scan_filesystem_sessions(cutoff_ts)
    stale_db = await _cleanup_stale_mongo_sessions(cutoff_ts)

    result = {
        "ran_at": now.isoformat(),
        "policy": policy,
        "deleted_sessions": fs["deleted_sessions"],
        "pruned_segments": fs["pruned_segments"],
        "freed_bytes": fs["freed_bytes"],
        "freed_gb": round(fs["freed_bytes"] / 1e9, 4),
        "stale_db_rows_marked": stale_db,
    }
    _last_pass_result = result

    if fs["deleted_sessions"] or fs["pruned_segments"] or stale_db:
        logger.info(
            f"[RETENTION] window={policy['label']}: "
            f"deleted {fs['deleted_sessions']} session(s), "
            f"pruned {fs['pruned_segments']} segment(s), "
            f"freed {result['freed_gb']:.3f} GB"
        )
    else:
        logger.debug(f"[RETENTION] pass complete — nothing to delete ({policy['label']})")

    return result
