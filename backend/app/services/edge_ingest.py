"""Edge clip search + media ingest into VMS HLS sessions (RDSO 18.3.16)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.core.database import (
    create_recording_session,
    update_recording_session,
)
from app.services.camera_uid import make_camera_uid
from app.services.edge_isapi import download_isapi_playback, search_isapi_recordings
from app.services.edge_onvif_recording import get_onvif_replay_uri, search_onvif_recordings
from app.services.edge_storage_types import PROTOCOL_ISAPI, PROTOCOL_ONVIF_G, parse_iso, to_iso
from app.services.rtsp_utils import ensure_rtsp_credentials
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.storage_settings_store import get_effective_recordings_dir
from app.services.storage_volume import assert_storage_ready_for_recording
from app.services.video_recording import _session_stats

logger = logging.getLogger(__name__)


async def search_edge_clips(
    camera: dict,
    start: datetime,
    end: datetime,
    *,
    protocol: Optional[str] = None,
) -> list[dict[str, Any]]:
    proto = (protocol or "").strip().lower()
    if proto in ("", "auto"):
        from app.services.edge_capability import detect_edge_storage_capability

        cap = await detect_edge_storage_capability(camera)
        if not cap.get("supported"):
            return []
        proto = str(cap.get("protocol") or "")

    if proto == PROTOCOL_ISAPI or proto == "hikvision_isapi":
        return await search_isapi_recordings(camera, start, end)
    if proto in (PROTOCOL_ONVIF_G, "onvif_profile_g", "onvif"):
        return await search_onvif_recordings(camera, start, end)
    return []


async def _run_ffmpeg(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    return proc.returncode or 0, (err or b"").decode("utf-8", errors="replace")[-2000:]


async def remux_file_to_hls(src: Path, session_dir: Path, *, segment_seconds: int = 4) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    playlist = session_dir / "index.m3u8"
    segment_pat = str(session_dir / "seg_%05d.ts")
    argv = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        str(max(1, int(segment_seconds))),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        segment_pat,
        str(playlist),
    ]
    rc, err = await _run_ffmpeg(argv)
    if rc != 0 or not playlist.is_file():
        # Fallback: re-encode if copy fails (codec mismatch)
        argv_re = [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-f",
            "hls",
            "-hls_time",
            str(max(1, int(segment_seconds))),
            "-hls_list_size",
            "0",
            "-hls_segment_filename",
            segment_pat,
            str(playlist),
        ]
        rc2, err2 = await _run_ffmpeg(argv_re)
        if rc2 != 0 or not playlist.is_file():
            raise RuntimeError(f"ffmpeg HLS ingest failed: {err or err2}")


async def ingest_edge_media_as_session(
    camera: dict,
    *,
    media_path: Path,
    clip_start: datetime,
    clip_end: datetime,
    job_id: str,
    protocol: str,
) -> dict[str, Any]:
    """Store downloaded edge footage as a normal stopped recording session."""
    assert_storage_ready_for_recording(create_if_missing=True)
    root = get_effective_recordings_dir()
    camera_id = str(camera.get("_id") or camera.get("id") or "")
    ip_address = (camera.get("ip_address") or "").strip()
    camera_uid = camera.get("camera_uid") or make_camera_uid(ip_address) or camera_id
    storage_folder = camera_uid

    session_meta = await create_recording_session(
        camera_id,
        storage_path=f"{storage_folder}/sessions",
        rtsp_url_masked=f"edge://{protocol}",
        camera_uid=camera_uid,
        camera_name=camera.get("name") or "",
        ip_address=ip_address,
        stream_profile=f"edge_backfill/{protocol}",
        segment_seconds="4",
    )
    session_id = session_meta["id"]
    rel_path = f"{storage_folder}/sessions/{session_id}"
    session_dir = root / storage_folder / "sessions" / session_id
    await remux_file_to_hls(media_path, session_dir)

    stats = _session_stats(session_dir)
    updates = {
        "status": "stopped",
        "started_at": to_iso(clip_start),
        "stopped_at": to_iso(clip_end),
        "storage_path": rel_path,
        "file_path": rel_path,
        "recordings_root": str(root),
        "storage_absolute_path": str(session_dir),
        "source": "edge_backfill",
        "edge_backfill_job_id": job_id,
        "edge_protocol": protocol,
        "playlist_file": "index.m3u8",
        "segment_count": stats.get("segment_count", 0),
        "total_bytes": stats.get("total_bytes", 0),
        "storage_used_gb": stats.get("storage_used_gb", 0.0),
        "latest_segment_time": stats.get("latest_segment_time"),
        "stop_reason": "edge_backfill",
    }
    updated = await update_recording_session(session_id, updates)
    try:
        from app.core.database import get_recording_session
        from app.services.evidence_integrity import attach_evidence_to_session

        sess = updated or {**session_meta, **updates, "id": session_id}
        await attach_evidence_to_session(session_id, session_dir, sess, force=False)
        updated = await get_recording_session(session_id) or updated
    except Exception as exc:
        logger.warning("[EVIDENCE] Edge session manifest failed %s: %s", session_id, exc)
    return updated or {**session_meta, **updates}


async def download_edge_clip_to_file(
    camera: dict,
    clip: dict[str, Any],
    dest: Path,
    *,
    protocol: str,
) -> int:
    """Download one edge clip. Supports ISAPI HTTP download or local mock path."""
    # Test / lab hook: clip may reference an already-available file
    local = clip.get("local_path") or clip.get("file_path")
    if local:
        src = Path(str(local))
        if not src.is_file():
            raise FileNotFoundError(f"Mock edge media missing: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest.stat().st_size

    uri = clip.get("playback_uri") or clip.get("playbackURI") or ""
    if protocol == PROTOCOL_ISAPI or uri:
        if str(uri).lower().startswith("rtsp://"):
            # Pull via ffmpeg into dest container
            dest.parent.mkdir(parents=True, exist_ok=True)
            argv = [
                ffmpeg_bin(),
                "-y",
                "-rtsp_transport",
                "tcp",
                "-i",
                str(uri),
                "-c",
                "copy",
                "-t",
                "3600",
                str(dest.with_suffix(".mkv")),
            ]
            rc, err = await _run_ffmpeg(argv)
            mkv = dest.with_suffix(".mkv")
            if rc != 0 or not mkv.is_file():
                raise RuntimeError(f"RTSP edge pull failed: {err}")
            if dest.suffix != ".mkv":
                shutil.move(str(mkv), str(dest))
            else:
                dest = mkv
            return Path(dest).stat().st_size
        return await download_isapi_playback(camera, str(uri), dest)

    if protocol == PROTOCOL_ONVIF_G:
        token = clip.get("recording_token") or clip.get("token")
        if not uri and token:
            replay_uri, replay_err = await get_onvif_replay_uri(camera, str(token))
            if replay_uri:
                uri = ensure_rtsp_credentials(
                    replay_uri,
                    (camera.get("username") or "admin").strip(),
                    camera.get("password") or "",
                )
            else:
                raise RuntimeError(
                    replay_err
                    or "ONVIF Profile G GetReplayUri unavailable; "
                    "edge media cannot be retrieved for this device"
                )
        if not uri:
            raise RuntimeError(
                "ONVIF edge clip has no downloadable media URI; "
                "device did not provide replay/download location"
            )
        if str(uri).lower().startswith("rtsp://"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            argv = [
                ffmpeg_bin(),
                "-y",
                "-rtsp_transport",
                "tcp",
                "-i",
                str(uri),
                "-c",
                "copy",
                "-t",
                "3600",
                str(dest.with_suffix(".mkv")),
            ]
            rc, err = await _run_ffmpeg(argv)
            mkv = dest.with_suffix(".mkv")
            if rc != 0 or not mkv.is_file():
                raise RuntimeError(f"ONVIF Replay RTSP pull failed: {err}")
            if dest.suffix != ".mkv":
                shutil.move(str(mkv), str(dest))
            else:
                dest = mkv
            return Path(dest).stat().st_size
        # Some devices return HTTP replay URLs
        return await download_isapi_playback(camera, str(uri), dest)

    raise RuntimeError(f"Cannot download edge clip for protocol={protocol}")
