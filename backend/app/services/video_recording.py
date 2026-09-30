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
    stopped_at = datetime.now(timezone.utc).isoformat()
    await update_recording_session(
        session_id,
        {
            "status": "stopped",
            "stopped_at": stopped_at,
            "stop_reason": stop_reason,
            "storage_path": f"{folder}/sessions/{session_id}",
            **stats,
        },
    )
    # RDSO 18.3.13 — seal evidence integrity for completed sessions
    try:
        from app.services.evidence_integrity import attach_evidence_to_session

        refreshed = await get_recording_session(session_id) or {
            "id": session_id,
            "camera_id": camera_id,
            "storage_path": f"{folder}/sessions/{session_id}",
            "stopped_at": stopped_at,
        }
        await attach_evidence_to_session(session_id, session_dir, refreshed, force=False)
    except Exception as exc:
        logging.warning(
            "[EVIDENCE] Manifest create failed session=%s: %s", session_id, exc
        )


async def finalize_orphaned_recording_sessions(
    *,
    stop_reason: str = "backend_restart",
) -> int:
    """Close MongoDB rows still marked recording with no in-memory FFmpeg.

    RDSO 18.3.3: only finalize sessions owned by this recording server (or legacy
    sessions without recording_server_id). Never close another server's live work.
    """
    from app.core.database import recording_sessions_collection
    from app.services.recording_server_config import local_recording_server_id, recording_ha_enabled

    active_ids = {entry["session_id"] for entry in ACTIVE_RECORDINGS.values()}
    local_id = local_recording_server_id()
    closed = 0
    async for doc in recording_sessions_collection.find({"status": "recording"}):
        session_id = str(doc["_id"])
        if session_id in active_ids:
            continue
        session_server = (doc.get("recording_server_id") or "").strip()
        if recording_ha_enabled() and session_server and session_server != local_id:
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
        self._spawn_started_monotonic: float = 0.0
        self._restart_failures: int = 0
        self._video_mode: str = "copy"
        self._source_codec: Optional[str] = None

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

        # RDSO 18.3.15 — ONVIF Profile S GetStreamUri when brand templates do not apply
        try:
            from app.services.onvif_stream_uri import (
                ensure_onvif_recording_urls,
                should_resolve_onvif_for_recording,
            )

            if should_resolve_onvif_for_recording(camera_doc):
                ensured = await ensure_onvif_recording_urls(camera_doc)
                if ensured.get("applied") and isinstance(ensured.get("camera"), dict):
                    camera_doc = ensured["camera"]
                elif not ensured.get("ok") and not (
                    camera_doc.get("main_rtsp_url") or camera_doc.get("sub_rtsp_url")
                ):
                    raise ValueError(
                        ensured.get("message")
                        or "ONVIF Profile S stream URI unavailable for recording"
                    )
        except ValueError:
            raise
        except Exception as exc:
            logging.warning(
                "[RECORDING] ONVIF stream resolve skipped for %s: %s",
                self.camera_id,
                exc,
            )

        urls = build_camera_rtsp_urls(camera_doc)
        rtsp_url, _label = resolve_recording_rtsp_url(camera_doc, urls)
        if not rtsp_url:
            raise ValueError(
                f"Camera {self.camera_id} has no recording RTSP URL ({recording_stream_profile()})"
            )

        self._rtsp_url = rtsp_url
        self.is_recording = True
        self._restart_failures = 0

        logging.info(
            f"[RECORDING] Session {self.session_id} for camera {self.camera_id}: "
            f"{mask_rtsp_url(rtsp_url)}"
        )
        await self._spawn_ffmpeg(rtsp_url)
        self._ensure_monitor_running()
        from app.services.recording_recovery import RECOVERY_RECORDING

        await self._persist_recovery(
            recovery_state=RECOVERY_RECORDING,
            restart_count=0,
            ffmpeg_alive=True,
        )

    def _ensure_monitor_running(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_recording_process())

    async def _spawn_ffmpeg(self, rtsp_url: str) -> None:
        """Start one FFmpeg HLS writer for this session (append_list). Does not start the monitor."""
        from app.services.recording_config import RECORDING_AUDIO_ENABLED
        from app.services.recording_codec import (
            VIDEO_MODE_COPY,
            VIDEO_MODE_ENCODE_H264,
            input_is_rtsp,
            probe_recording_video_codec,
            video_encode_args_for_mode,
        )

        playlist_path = str(self.session_dir / "index.m3u8")
        segment_pattern = str(self.session_dir / "seg_%05d.ts")

        # Detect source codec once per spawn so MJPEG gets H.264 archive encode;
        # H.264/H.265 stay stream-copy.
        probe = await probe_recording_video_codec(rtsp_url)
        video_mode = probe.get("video_mode") or VIDEO_MODE_COPY
        self._video_mode = video_mode
        self._source_codec = probe.get("codec_name")
        if video_mode == VIDEO_MODE_ENCODE_H264:
            logging.info(
                "[RECORDING] MJPEG/JPEG source detected (%s) — encoding to H.264 for HLS archive "
                "camera=%s session=%s",
                self._source_codec or "unknown",
                self.camera_id,
                self.session_id,
            )
        elif probe.get("ok"):
            logging.info(
                "[RECORDING] Source codec=%s mode=copy camera=%s session=%s",
                self._source_codec,
                self.camera_id,
                self.session_id,
            )

        # append_list + program_date_time — full timeline kept; PDT uses OS clock
        hls_flags = "append_list+program_date_time+independent_segments"
        list_size = str(RECORDING_LIST_SIZE)
        ffmpeg_cmd = [
            FFMPEG,
            "-hide_banner",
            "-loglevel", "warning",
            "-probesize", "512000",
            "-analyzeduration", "500000",
        ]
        if input_is_rtsp(rtsp_url):
            ffmpeg_cmd.extend(["-rtsp_transport", "tcp", *_rtsp_timeout_args()])
        ffmpeg_cmd.extend(
            [
                "-i", rtsp_url,
                "-map", "0:v:0",
            ]
        )
        ffmpeg_cmd.extend(video_encode_args_for_mode(video_mode))

        if RECORDING_AUDIO_ENABLED:
            # Optional audio map: video-only sources still record successfully.
            ffmpeg_cmd.extend(
                [
                    "-map", "0:a:0?",
                    "-c:a", "aac",
                    "-ac", "1",
                    "-ar", "16000",
                    "-b:a", "64k",
                ]
            )
        else:
            ffmpeg_cmd.append("-an")

        ffmpeg_cmd.extend(
            [
                "-f", "hls",
                "-hls_time", RECORDING_SEGMENT_SECONDS,
                "-hls_list_size", list_size,
                "-hls_flags", hls_flags,
                "-hls_segment_filename", segment_pattern,
                playlist_path,
            ]
        )

        logging.info(
            "[RECORDING] FFmpeg HLS → %s video_mode=%s source_codec=%s audio=%s",
            playlist_path,
            video_mode,
            self._source_codec or "unknown",
            "aac_when_present" if RECORDING_AUDIO_ENABLED else "off",
        )

        # Replace prior stderr reader if any
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except Exception:
                pass

        self.recording_process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        self._spawn_started_monotonic = asyncio.get_running_loop().time()
        self._stderr_task = asyncio.create_task(self._read_ffmpeg_stderr())

        # Best-effort: persist encode mode on the live session for operators/export.
        try:
            await update_recording_session(
                self.session_id,
                {
                    "source_video_codec": self._source_codec,
                    "recording_video_mode": video_mode,
                    "recording_video_transcoded": video_mode == VIDEO_MODE_ENCODE_H264,
                },
            )
        except Exception:
            pass

    async def _persist_recovery(
        self,
        *,
        recovery_state: str,
        restart_count: Optional[int] = None,
        last_failure_reason: Optional[str] = None,
        ffmpeg_alive: Optional[bool] = None,
    ) -> None:
        from app.services.recording_recovery import persist_recording_recovery_status

        await persist_recording_recovery_status(
            self.session_id,
            recovery_state=recovery_state,
            restart_count=restart_count,
            last_failure_reason=last_failure_reason,
            ffmpeg_alive=ffmpeg_alive,
        )

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
        """On unexpected FFmpeg exit, restart the same session with bounded backoff."""
        from app.services.recording_config import RECORDING_RESTART_STABLE_SECONDS
        from app.services.recording_recovery import (
            RECOVERY_BACKOFF,
            RECOVERY_RECONNECTING,
            RECOVERY_RECORDING,
            compute_restart_backoff_seconds,
        )

        while self.is_recording:
            proc = self.recording_process
            if proc is None:
                if not self._rtsp_url:
                    return
                self._restart_failures = max(1, self._restart_failures)
                delay = compute_restart_backoff_seconds(self._restart_failures)
                await self._persist_recovery(
                    recovery_state=RECOVERY_BACKOFF,
                    restart_count=self._restart_failures,
                    last_failure_reason="ffmpeg_missing",
                    ffmpeg_alive=False,
                )
                logging.error(
                    "[RECORDING] No FFmpeg process camera=%s session=%s — retry in %.1fs (failure=%s)",
                    self.camera_id,
                    self.session_id,
                    delay,
                    self._restart_failures,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                if not self.is_recording:
                    return
                try:
                    await self._spawn_ffmpeg(self._rtsp_url)
                    await self._persist_recovery(
                        recovery_state=RECOVERY_RECONNECTING,
                        restart_count=self._restart_failures,
                        ffmpeg_alive=True,
                    )
                    self._schedule_stable_recovery_mark()
                except Exception as exc:
                    self._restart_failures += 1
                    logging.error(
                        "[RECORDING] Respawn failed camera=%s: %s",
                        self.camera_id,
                        exc,
                        exc_info=True,
                    )
                    await self._persist_recovery(
                        recovery_state=RECOVERY_BACKOFF,
                        restart_count=self._restart_failures,
                        last_failure_reason=f"spawn_error:{exc}",
                        ffmpeg_alive=False,
                    )
                continue

            try:
                rc = await proc.wait()
            except asyncio.CancelledError:
                return

            if not self.is_recording or self.recording_process != proc:
                return

            await self._cleanup_process(proc)
            if self.recording_process == proc:
                self.recording_process = None

            lived = 0.0
            if self._spawn_started_monotonic:
                lived = asyncio.get_running_loop().time() - self._spawn_started_monotonic
            if lived >= float(RECORDING_RESTART_STABLE_SECONDS):
                # Was healthy long enough — treat this as a fresh failure streak.
                self._restart_failures = 0
            self._restart_failures += 1
            delay = compute_restart_backoff_seconds(self._restart_failures)

            logging.error(
                "[RECORDING] FFmpeg exited camera=%s session=%s code=%s lived=%.1fs — "
                "restart in %.1fs (failure=%s)",
                self.camera_id,
                self.session_id,
                rc,
                lived,
                delay,
                self._restart_failures,
            )
            await self._persist_recovery(
                recovery_state=RECOVERY_BACKOFF,
                restart_count=self._restart_failures,
                last_failure_reason=f"ffmpeg_exit_{rc}",
                ffmpeg_alive=False,
            )

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not self.is_recording:
                return
            if not self._rtsp_url:
                return

            try:
                await self._spawn_ffmpeg(self._rtsp_url)
                await self._persist_recovery(
                    recovery_state=RECOVERY_RECONNECTING,
                    restart_count=self._restart_failures,
                    ffmpeg_alive=True,
                )
                self._schedule_stable_recovery_mark()
            except Exception as exc:
                self._restart_failures += 1
                logging.error(
                    "[RECORDING] Restart spawn failed camera=%s: %s",
                    self.camera_id,
                    exc,
                    exc_info=True,
                )
                await self._persist_recovery(
                    recovery_state=RECOVERY_BACKOFF,
                    restart_count=self._restart_failures,
                    last_failure_reason=f"spawn_error:{exc}",
                    ffmpeg_alive=False,
                )

    def _schedule_stable_recovery_mark(self) -> None:
        """After a successful respawn survives the stable window, clear the failure streak."""
        proc = self.recording_process
        if proc is None:
            return

        async def _worker() -> None:
            from app.services.recording_config import RECORDING_RESTART_STABLE_SECONDS
            from app.services.recording_recovery import RECOVERY_RECORDING

            try:
                await asyncio.sleep(float(RECORDING_RESTART_STABLE_SECONDS))
            except asyncio.CancelledError:
                return
            if (
                self.is_recording
                and self.recording_process is proc
                and proc.returncode is None
            ):
                self._restart_failures = 0
                await self._persist_recovery(
                    recovery_state=RECOVERY_RECORDING,
                    restart_count=0,
                    ffmpeg_alive=True,
                )

        asyncio.create_task(_worker())

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
    from app.services.recording_config import (
        RECORDING_MAX_CONCURRENT,
        RecordingEngineDisabled,
        is_recording_engine_enabled,
    )

    if not is_recording_engine_enabled():
        raise RecordingEngineDisabled("Recording engine is disabled")

    # RDSO 18.3.3 — exclusive ownership before starting FFmpeg (prevents split-brain)
    try:
        from app.services.recording_ha_coordinator import local_may_record_camera

        gate = await local_may_record_camera(camera_id)
        if not gate.get("allowed"):
            owner = (gate.get("ownership") or {}).get("owner_server_id")
            raise RuntimeError(
                f"Camera {camera_id} is owned by recording server "
                f"{owner or 'another node'}; refusing duplicate start"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        logging.warning("[HA] Ownership gate skipped for %s: %s", camera_id, exc)

    if camera_id not in _start_locks:
        _start_locks[camera_id] = asyncio.Lock()

    async with _start_locks[camera_id]:
        if camera_id in ACTIVE_RECORDINGS:
            session_id = ACTIVE_RECORDINGS[camera_id]["session_id"]
            existing = await get_recording_session(session_id)
            if existing:
                return existing
            raise RuntimeError(f"Recording active for {camera_id} but session missing")

        # Re-check ownership inside lock
        try:
            from app.services.recording_ha_coordinator import local_may_record_camera

            gate = await local_may_record_camera(camera_id)
            if not gate.get("allowed"):
                owner = (gate.get("ownership") or {}).get("owner_server_id")
                raise RuntimeError(
                    f"Camera {camera_id} is owned by recording server "
                    f"{owner or 'another node'}; refusing duplicate start"
                )
        except RuntimeError:
            raise
        except Exception:
            pass

        limit = int(RECORDING_MAX_CONCURRENT or 0)
        if limit > 0:
            active_count = sum(
                1
                for entry in ACTIVE_RECORDINGS.values()
                if entry.get("recorder") is not None
                and getattr(entry["recorder"], "is_recording", False)
            )
            if active_count >= limit:
                raise RuntimeError(
                    f"Concurrent recording limit reached ({active_count}/{limit}). "
                    "Raise or clear RECORDING_MAX_CONCURRENT (0 = unlimited)."
                )

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

        # RDSO 18.1.12 — configured storage only; never fall back to another disk.
        from app.services.storage_settings_store import get_effective_recordings_dir
        from app.services.storage_volume import assert_storage_ready_for_recording

        storage_probe = assert_storage_ready_for_recording(create_if_missing=True)
        recordings_root = str(get_effective_recordings_dir())

        try:
            from app.services.onvif_stream_uri import (
                ensure_onvif_recording_urls,
                should_resolve_onvif_for_recording,
            )

            if should_resolve_onvif_for_recording(camera_doc):
                ensured = await ensure_onvif_recording_urls(camera_doc)
                if ensured.get("applied") and isinstance(ensured.get("camera"), dict):
                    camera_doc = ensured["camera"]
        except Exception as exc:
            logging.warning("[RECORDING] ONVIF pre-resolve skipped for %s: %s", camera_id, exc)

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
        abs_path = str(Path(recordings_root) / rel_path)
        from app.services.recording_ha_coordinator import ensure_session_server_fields
        from app.services.recording_server_config import local_recording_server_id, local_server_role

        server_fields = await ensure_session_server_fields()
        await update_recording_session(
            session_id,
            {
                "storage_path": rel_path,
                "file_path": rel_path,
                "recordings_root": recordings_root,
                "storage_absolute_path": abs_path,
                "storage_status_at_start": storage_probe.get("status"),
                **server_fields,
            },
        )
        session_meta = {
            **session_meta,
            "storage_path": rel_path,
            "file_path": rel_path,
            "recordings_root": recordings_root,
            "storage_absolute_path": abs_path,
            "recording_server_id": server_fields.get("recording_server_id")
            or local_recording_server_id(),
            "recording_server_role": server_fields.get("recording_server_role")
            or local_server_role(),
        }

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
        try:
            from app.services.recording_ha_coordinator import set_recording_active_flag

            await set_recording_active_flag(camera_id, True)
        except Exception:
            pass
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
    try:
        from app.services.recording_ha_coordinator import set_recording_active_flag

        await set_recording_active_flag(camera_id, False)
    except Exception:
        pass

    stats = _session_stats(recorder.session_dir)
    stopped_at = datetime.now(timezone.utc).isoformat()
    updated = await update_recording_session(
        session_id,
        {
            "status": "stopped",
            "stopped_at": stopped_at,
            "storage_path": f"{recorder.storage_folder}/sessions/{session_id}",
            **stats,
        },
    )
    try:
        from app.services.evidence_integrity import attach_evidence_to_session

        sess = updated or {
            "id": session_id,
            "camera_id": camera_id,
            "storage_path": f"{recorder.storage_folder}/sessions/{session_id}",
            "stopped_at": stopped_at,
        }
        await attach_evidence_to_session(
            session_id, recorder.session_dir, sess, force=False
        )
        updated = await get_recording_session(session_id) or updated
    except Exception as exc:
        logging.warning("[EVIDENCE] Manifest create failed session=%s: %s", session_id, exc)
    return updated


async def is_camera_recording(camera_id: str) -> bool:
    return camera_id in ACTIVE_RECORDINGS and ACTIVE_RECORDINGS[camera_id]["recorder"].is_recording


async def ensure_recording_process_alive(camera_id: str) -> str:
    """Safety net: restart a dead monitor for an owned ACTIVE_RECORDINGS entry.

    Does not create a second session or second ACTIVE_RECORDINGS slot.
    Returns: ok | not_active | monitor_restarted | degraded
    """
    entry = ACTIVE_RECORDINGS.get(camera_id)
    if not entry:
        return "not_active"
    recorder: VideoRecorder = entry["recorder"]
    if not recorder.is_recording:
        return "not_active"

    proc = recorder.recording_process
    proc_alive = proc is not None and proc.returncode is None
    monitor = recorder._monitor_task
    monitor_ok = monitor is not None and not monitor.done()

    if proc_alive and monitor_ok:
        return "ok"

    if not monitor_ok:
        logging.warning(
            "[RECORDING] Monitor dead for camera=%s session=%s proc_alive=%s — restarting monitor",
            camera_id,
            recorder.session_id,
            proc_alive,
        )
        recorder._ensure_monitor_running()
        return "monitor_restarted"

    # Monitor still running (likely in backoff) while process is down.
    return "degraded"


def recording_process_pid(camera_id: str) -> Optional[int]:
    """Return active FFmpeg PID if present (for tests / diagnostics)."""
    entry = ACTIVE_RECORDINGS.get(camera_id)
    if not entry:
        return None
    recorder: VideoRecorder = entry["recorder"]
    proc = recorder.recording_process
    if proc is None or proc.returncode is not None:
        return None
    return proc.pid


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
