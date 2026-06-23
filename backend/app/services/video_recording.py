import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from bson import ObjectId

from app.core.database import (
    camera_collection,
    create_recording_session,
    get_active_recording_session,
    get_recording_session,
    update_recording_session,
)
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.rtsp_utils import build_camera_rtsp_urls, mask_rtsp_url
from app.services.camera_uid import make_camera_uid
from app.services.recording_config import (
    RECORDING_SEGMENT_SECONDS,
    RECORDING_LIST_SIZE,
    resolve_recording_rtsp_url,
    recording_stream_profile,
)

FFMPEG = ffmpeg_bin()

# ----------------------------
# Recording Configuration
# ----------------------------

if os.getenv("RECORDINGS_DIR"):
    RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR")).resolve()
else:
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    RECORDINGS_DIR = project_root / "Recordings"

RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
logging.info(f"[RECORDING] Recordings will be stored in: {RECORDINGS_DIR.absolute()}")

logging.info(
    f"[RECORDING] stream={recording_stream_profile()}, "
    f"segment={RECORDING_SEGMENT_SECONDS}s, retention via RECORDING_RETENTION_HOURS/DAYS"
)

ACTIVE_RECORDINGS: Dict[str, Dict] = {}  # camera_id -> {recorder, session_id, started_at}
_start_locks: Dict[str, asyncio.Lock] = {}


def _rtsp_timeout_args() -> list:
    flag = "-timeout" if os.name == "nt" else "-stimeout"
    args = [flag, "5000000"]
    if os.name != "nt":
        args.extend(["-rw_timeout", "5000000"])
    return args


def session_storage_dir(camera_id: str, session_id: str) -> Path:
    return RECORDINGS_DIR / camera_id / "sessions" / session_id


def storage_folder_from_path(storage_path: str | None, fallback: str) -> str:
    path = (storage_path or "").strip()
    if path:
        return path.split("/", 1)[0]
    return fallback


def session_dir_for_folder(storage_folder: str, session_id: str) -> Path:
    return session_storage_dir(storage_folder, session_id)


def _empty_session_stats() -> Dict:
    return {
        "segment_count": 0,
        "total_bytes": 0,
        "storage_used_gb": 0.0,
        "latest_segment_time": None,
    }


def _session_stats(session_dir: Path) -> Dict:
    """Read segment_count, total_bytes, storage_used_gb, latest_segment_time from disk."""
    if not session_dir.is_dir():
        return _empty_session_stats()
    segments = list(session_dir.glob("seg_*.ts"))
    if not segments:
        segments = list(session_dir.glob("*.ts"))
    if not segments:
        return _empty_session_stats()
    total_bytes = sum(f.stat().st_size for f in segments)
    latest_mtime = max(f.stat().st_mtime for f in segments)
    return {
        "segment_count": len(segments),
        "total_bytes": total_bytes,
        "storage_used_gb": round(total_bytes / 1e9, 4),
        "latest_segment_time": datetime.fromtimestamp(
            latest_mtime, tz=timezone.utc
        ).isoformat(),
    }


async def sync_session_stats_to_db(
    camera_id: str,
    session_id: str,
    *,
    extra: Optional[dict] = None,
) -> Optional[dict]:
    """Persist filesystem-derived stats to recording_sessions in MongoDB."""
    stats = _session_stats(session_storage_dir(camera_id, session_id))
    updates = {
        **stats,
        "last_stats_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        updates.update(extra)
    return await update_recording_session(session_id, updates)


async def _finalize_recording_session(
    camera_id: str,
    session_id: str,
    *,
    stop_reason: str,
    storage_folder: str | None = None,
) -> None:
    """Mark a session stopped and persist segment stats from disk."""
    from app.core.database import get_recording_session

    session = await get_recording_session(session_id)
    folder = storage_folder or storage_folder_from_path(
        (session or {}).get("storage_path"),
        camera_id,
    )
    session_dir = session_dir_for_folder(folder, session_id)
    stats = _session_stats(session_dir)
    await update_recording_session(
        session_id,
        {
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "stop_reason": stop_reason,
            "storage_path": f"{folder}/sessions/{session_id}",
            **stats,
        },
    )


async def finalize_orphaned_recording_sessions(
    *,
    stop_reason: str = "backend_restart",
) -> int:
    """Close all MongoDB rows still marked recording (no in-memory FFmpeg)."""
    from app.core.database import recording_sessions_collection

    active_ids = {entry["session_id"] for entry in ACTIVE_RECORDINGS.values()}
    closed = 0
    async for doc in recording_sessions_collection.find({"status": "recording"}):
        session_id = str(doc["_id"])
        if session_id in active_ids:
            continue
        await _finalize_recording_session(
            doc.get("camera_id") or "",
            session_id,
            stop_reason=stop_reason,
            storage_folder=storage_folder_from_path(doc.get("storage_path"), doc.get("camera_id") or ""),
        )
        closed += 1
    if closed:
        logging.info(f"[RECORDING] Finalized {closed} orphaned session(s) ({stop_reason})")
    return closed


async def reconcile_stale_db_sessions() -> int:
    """Close duplicate recording rows that are not the live in-memory session."""
    from app.core.database import recording_sessions_collection

    live_by_camera = {
        camera_id: entry["session_id"] for camera_id, entry in ACTIVE_RECORDINGS.items()
    }
    closed = 0
    async for doc in recording_sessions_collection.find({"status": "recording"}):
        camera_id = doc["camera_id"]
        session_id = str(doc["_id"])
        live_session = live_by_camera.get(camera_id)
        if live_session is not None:
            if session_id == live_session:
                continue
        else:
            newest = await get_active_recording_session(camera_id)
            if newest and session_id == newest["id"]:
                continue
        await _finalize_recording_session(
            camera_id,
            session_id,
            stop_reason="superseded",
        )
        closed += 1
    return closed


class VideoRecorder:
    """RTSP → HLS session recorder; writes under Recordings/{storage_folder}/sessions/{session_id}/."""

    def __init__(self, camera_id: str, session_id: str, *, storage_folder: str | None = None):
        self.camera_id = camera_id
        self.storage_folder = storage_folder or camera_id
        self.session_id = session_id
        self.session_dir = session_storage_dir(self.storage_folder, session_id)
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.recording_process: Optional[asyncio.subprocess.Process] = None
        self.is_recording: bool = False
        self._stderr_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._rtsp_url: Optional[str] = None

    async def start_recording(self):
        if self.is_recording:
            logging.warning(f"[RECORDING] Recording already active for camera {self.camera_id}")
            return

        cam_oid = ObjectId(self.camera_id)
        camera_doc = await camera_collection.find_one({"_id": cam_oid})
        if not camera_doc:
            raise ValueError(f"Camera {self.camera_id} not found in database")

        ip_address = (camera_doc.get("ip_address") or "").strip()
        if not ip_address:
            raise ValueError(f"Camera {self.camera_id} missing IP address")

        password = camera_doc.get("password")
        if password is None or str(password).strip() == "":
            raise ValueError(f"Camera {self.camera_id} has missing password")

        urls = build_camera_rtsp_urls(camera_doc)
        rtsp_url, _label = resolve_recording_rtsp_url(camera_doc, urls)
        if not rtsp_url:
            raise ValueError(
                f"Camera {self.camera_id} has no recording RTSP URL ({recording_stream_profile()})"
            )

        self._rtsp_url = rtsp_url
        self.is_recording = True

        logging.info(
            f"[RECORDING] Session {self.session_id} for camera {self.camera_id}: "
            f"{mask_rtsp_url(rtsp_url)}"
        )
        await self._start_recording_process(rtsp_url)

    async def _start_recording_process(self, rtsp_url: str):
        playlist_path = str(self.session_dir / "index.m3u8")
        segment_pattern = str(self.session_dir / "seg_%05d.ts")

        # append_list + no delete on Windows — full 24h timeline kept on disk
        if os.name == "nt":
            hls_flags = "append_list+program_date_time+independent_segments"
        else:
            hls_flags = "append_list+program_date_time+independent_segments"
        list_size = str(RECORDING_LIST_SIZE)
        ffmpeg_cmd = [
            FFMPEG,
            "-hide_banner",
            "-loglevel", "warning",
            "-probesize", "512000",
            "-analyzeduration", "500000",
            "-rtsp_transport", "tcp",
            *_rtsp_timeout_args(),
            "-i", rtsp_url,
            "-an",
            "-c:v", "copy",
            "-f", "hls",
            "-hls_time", RECORDING_SEGMENT_SECONDS,
            "-hls_list_size", list_size,
            "-hls_flags", hls_flags,
            "-hls_segment_filename", segment_pattern,
            playlist_path,
        ]

        logging.info(f"[RECORDING] FFmpeg HLS → {playlist_path}")

        self.recording_process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )

        self._stderr_task = asyncio.create_task(self._read_ffmpeg_stderr())
        self._monitor_task = asyncio.create_task(self._monitor_recording_process())

    async def _read_ffmpeg_stderr(self):
        proc = self.recording_process
        if not proc or not proc.stderr:
            return
        try:
            while self.is_recording and self.recording_process == proc:
                line = await proc.stderr.readline()
                if not line:
                    break
                msg = line.decode("utf-8", errors="ignore").strip()
                if not msg:
                    continue
                lower = msg.lower()
                if "error" in lower or "failed" in lower:
                    logging.error(f"[RECORDING][ffmpeg][{self.camera_id}] {msg}")
                elif "warning" in lower:
                    logging.warning(f"[RECORDING][ffmpeg][{self.camera_id}] {msg}")
        except asyncio.CancelledError:
            return

    async def _monitor_recording_process(self):
        proc = self.recording_process
        if not proc:
            return
        try:
            rc = await proc.wait()
            if self.is_recording and self.recording_process == proc:
                logging.error(
                    f"[RECORDING] FFmpeg exited for camera {self.camera_id} (code={rc}). Restarting..."
                )
                await self._cleanup_process(proc)
                if self._rtsp_url and self.is_recording:
                    await asyncio.sleep(1.5)
                    await self._start_recording_process(self._rtsp_url)
        except asyncio.CancelledError:
            return

    async def _cleanup_process(self, proc: asyncio.subprocess.Process):
        for stream in (proc.stdin, proc.stderr):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass

    async def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False

        for task in (self._stderr_task, self._monitor_task):
            if task:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

        self._stderr_task = None
        self._monitor_task = None

        proc = self.recording_process
        self.recording_process = None

        if proc:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (asyncio.TimeoutError, Exception):
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception as e:
                logging.error(f"[RECORDING] Error stopping ffmpeg for {self.camera_id}: {e}")
            finally:
                await self._cleanup_process(proc)

        logging.info(f"[RECORDING] Stopped session {self.session_id} for camera '{self.camera_id}'")

    def get_hls_info(self) -> Dict:
        playlist = self.session_dir / "index.m3u8"
        rel = None
        if playlist.exists():
            rel = str(playlist.relative_to(RECORDINGS_DIR))
        stats = _session_stats(self.session_dir)
        return {
            "camera_id": self.camera_id,
            "session_id": self.session_id,
            "playlist_exists": playlist.exists(),
            "playlist_path": rel,
            "storage_path": str(self.session_dir.relative_to(RECORDINGS_DIR)),
            "updated_at": datetime.fromtimestamp(playlist.stat().st_mtime).isoformat()
            if playlist.exists()
            else None,
            **stats,
        }


# ----------------------------
# Public management functions
# ----------------------------

async def start_camera_recording(camera_id: str) -> dict:
    """Start an RTSP recording session; returns session metadata."""
    if camera_id not in _start_locks:
        _start_locks[camera_id] = asyncio.Lock()

    async with _start_locks[camera_id]:
        if camera_id in ACTIVE_RECORDINGS:
            session_id = ACTIVE_RECORDINGS[camera_id]["session_id"]
            existing = await get_recording_session(session_id)
            if existing:
                return existing
            raise RuntimeError(f"Recording active for {camera_id} but session missing")

        active = await get_active_recording_session(camera_id)
        if active:
            if camera_id in ACTIVE_RECORDINGS:
                return active
            # Stale DB row (FFmpeg not running after restart) — sync disk stats then restart
            logging.warning(
                f"[RECORDING] Stale session {active['id']} for {camera_id} — restarting FFmpeg"
            )
            await _finalize_recording_session(
                camera_id,
                active["id"],
                stop_reason="stale_session_recovery",
            )

        cam_oid = ObjectId(camera_id)
        camera_doc = await camera_collection.find_one({"_id": cam_oid})
        if not camera_doc:
            raise ValueError(f"Camera {camera_id} not found")

        urls = build_camera_rtsp_urls(camera_doc)
        rec_url, _label = resolve_recording_rtsp_url(camera_doc, urls)
        rtsp_masked = mask_rtsp_url(rec_url or "")

        ip_address = (camera_doc.get("ip_address") or "").strip()
        camera_uid = camera_doc.get("camera_uid") or make_camera_uid(ip_address) or camera_id
        storage_folder = camera_uid

        session_meta = await create_recording_session(
            camera_id,
            storage_path=f"{storage_folder}/sessions",
            rtsp_url_masked=rtsp_masked,
            camera_uid=camera_uid,
            camera_name=camera_doc.get("name") or "",
            ip_address=ip_address,
            stream_profile=recording_stream_profile(),
            segment_seconds=RECORDING_SEGMENT_SECONDS,
        )
        session_id = session_meta["id"]
        rel_path = f"{storage_folder}/sessions/{session_id}"
        await update_recording_session(
            session_id,
            {"storage_path": rel_path, "file_path": rel_path},
        )

        recorder = VideoRecorder(camera_id, session_id, storage_folder=storage_folder)
        try:
            await recorder.start_recording()
        except Exception:
            await update_recording_session(
                session_id,
                {
                    "status": "failed",
                    "stopped_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise

        ACTIVE_RECORDINGS[camera_id] = {
            "recorder": recorder,
            "session_id": session_id,
            "started_at": session_meta["started_at"],
        }
        return session_meta


async def stop_camera_recording(camera_id: str) -> Optional[dict]:
    """Stop recording and persist session stats to MongoDB."""
    if camera_id not in ACTIVE_RECORDINGS:
        active = await get_active_recording_session(camera_id)
        if active:
            await _finalize_recording_session(
                camera_id,
                active["id"],
                stop_reason="stop_without_ffmpeg",
            )
            return await get_recording_session(active["id"])
        return active

    entry = ACTIVE_RECORDINGS[camera_id]
    recorder: VideoRecorder = entry["recorder"]
    session_id = entry["session_id"]

    await recorder.stop_recording()
    del ACTIVE_RECORDINGS[camera_id]

    stats = _session_stats(recorder.session_dir)
    stopped_at = datetime.now(timezone.utc).isoformat()
    return await update_recording_session(
        session_id,
        {
            "status": "stopped",
            "stopped_at": stopped_at,
            "storage_path": f"{recorder.storage_folder}/sessions/{session_id}",
            **stats,
        },
    )


async def is_camera_recording(camera_id: str) -> bool:
    return camera_id in ACTIVE_RECORDINGS and ACTIVE_RECORDINGS[camera_id]["recorder"].is_recording


async def get_camera_hls_info(camera_id: str) -> Dict:
    if camera_id in ACTIVE_RECORDINGS:
        recorder: VideoRecorder = ACTIVE_RECORDINGS[camera_id]["recorder"]
        return recorder.get_hls_info()

    active = await get_active_recording_session(camera_id)
    if active:
        session_dir = session_storage_dir(camera_id, active["id"])
        playlist = session_dir / "index.m3u8"
        stats = _session_stats(session_dir)
        return {
            "camera_id": camera_id,
            "session_id": active["id"],
            "playlist_exists": playlist.exists(),
            "playlist_path": str(playlist.relative_to(RECORDINGS_DIR)) if playlist.exists() else None,
            "storage_path": active.get("storage_path"),
            "updated_at": datetime.fromtimestamp(playlist.stat().st_mtime).isoformat()
            if playlist.exists()
            else None,
            **stats,
        }

    camera_dir = RECORDINGS_DIR / camera_id
    playlist = camera_dir / "index.m3u8"
    return {
        "camera_id": camera_id,
        "playlist_exists": playlist.exists(),
        "playlist_path": str(playlist.relative_to(RECORDINGS_DIR)) if playlist.exists() else None,
        "updated_at": datetime.fromtimestamp(playlist.stat().st_mtime).isoformat()
        if playlist.exists()
        else None,
    }


async def cleanup_all_recordings():
    logging.info("[RECORDING] Stopping all active recordings...")
    for camera_id in list(ACTIVE_RECORDINGS.keys()):
        try:
            await stop_camera_recording(camera_id)
        except Exception as e:
            logging.error(f"[RECORDING] Error stopping recording for {camera_id}: {e}")
    logging.info("[RECORDING] All recordings stopped.")
