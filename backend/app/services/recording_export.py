"""Interval recording export — remux selected [start, end] to offline MP4 (copy when safe)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.services.app_timezone import get_effective_app_timezone_name
from app.services.camera_identity import resolve_camera_uid
from app.services.ffmpeg_util import ffmpeg_bin
from app.services.playback_search import (
    MAX_MULTI_PLAYBACK_CAMERAS,
    _parse_iso,
    search_recordings_by_date,
)
from app.services.recording_media import RecordingMediaError, resolve_session_dir
from app.services.storage_settings_store import get_effective_recordings_dir

logger = logging.getLogger(__name__)

MAX_EXPORT_CAMERAS = MAX_MULTI_PLAYBACK_CAMERAS
MAX_EXPORT_SECONDS = 4 * 3600  # 4 hours safety cap
EXPORT_FFMPEG_TIMEOUT_SEC = 600

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_filename(label: str, suffix: str = "") -> str:
    base = _SAFE_NAME.sub("_", (label or "camera").strip())[:80] or "camera"
    return f"{base}{suffix}"


def _local_dates_covering(start: datetime, end: datetime) -> list[str]:
    """Calendar YYYY-MM-DD values in APP_TIMEZONE that intersect [start, end)."""
    from app.services.app_timezone import get_app_timezone

    start = _utc(start)
    end = _utc(end)
    if end <= start:
        return []
    tz = get_app_timezone()
    d0 = start.astimezone(tz).date()
    d1 = end.astimezone(tz).date()
    dates: list[str] = []
    cur = d0
    while cur <= d1:
        dates.append(cur.isoformat())
        cur = cur + timedelta(days=1)
    return dates


def _overlap_piece(
    rec: dict,
    range_start: datetime,
    range_end: datetime,
) -> Optional[dict]:
    rec_start = _parse_iso(rec.get("startTime"))
    rec_end = _parse_iso(rec.get("endTime"))
    if rec_start is None:
        return None
    rec_start = _utc(rec_start)
    if rec_end is None:
        rec_end = rec_start
    else:
        rec_end = _utc(rec_end)
    # Filesystem entries with one segment can have equal start/end; use duration.
    declared = float(rec.get("duration") or 0)
    if declared > 0 and (rec_end - rec_start).total_seconds() < max(1.0, declared * 0.5):
        rec_end = rec_start + timedelta(seconds=declared)
    if rec_end <= rec_start:
        # Last resort: assume at least one HLS segment (~2–300s); use 2s minimum for export.
        rec_end = rec_start + timedelta(seconds=max(2.0, declared or 2.0))

    range_start = _utc(range_start)
    range_end = _utc(range_end)
    ov_start = max(rec_start, range_start)
    ov_end = min(rec_end, range_end)
    if ov_end <= ov_start:
        return None
    offset = max(0.0, (ov_start - rec_start).total_seconds())
    duration = max(0.0, (ov_end - ov_start).total_seconds())
    if duration < 0.2:
        return None
    return {
        "sessionId": rec.get("sessionId"),
        "playlistUrl": rec.get("playlistUrl"),
        "filePath": rec.get("filePath"),
        "offsetSeconds": offset,
        "durationSeconds": duration,
        "clipStart": ov_start.isoformat(),
        "clipEnd": ov_end.isoformat(),
        "sessionStart": rec.get("startTime"),
        "sessionEnd": rec.get("endTime"),
    }


def compute_gaps(
    range_start: datetime,
    range_end: datetime,
    pieces: list[dict],
) -> list[dict]:
    """Return uncovered sub-intervals within [range_start, range_end]."""
    range_start = _utc(range_start)
    range_end = _utc(range_end)
    covered: list[tuple[datetime, datetime]] = []
    for p in pieces:
        a = _parse_iso(p.get("clipStart"))
        b = _parse_iso(p.get("clipEnd"))
        if a is None or b is None:
            continue
        covered.append((_utc(a), _utc(b)))
    covered.sort(key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = []
    for a, b in covered:
        if not merged or a > merged[-1][1]:
            merged.append((a, b))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))

    gaps: list[dict] = []
    cursor = range_start
    for a, b in merged:
        if a > cursor:
            gaps.append(
                {
                    "start": cursor.isoformat(),
                    "end": a.isoformat(),
                    "reason": "no_footage",
                }
            )
        cursor = max(cursor, b)
    if cursor < range_end:
        gaps.append(
            {
                "start": cursor.isoformat(),
                "end": range_end.isoformat(),
                "reason": "no_footage",
            }
        )
    return gaps


def _folder_and_session_from_rec(rec: dict) -> tuple[str, str]:
    session_id = str(rec.get("sessionId") or "")
    path = str(rec.get("filePath") or "")
    if path:
        folder = path.split("/", 1)[0]
        if folder:
            return folder, session_id
    playlist = str(rec.get("playlistUrl") or "")
    # /api/playback/{folder}/{session}/media/index.m3u8
    parts = playlist.strip("/").split("/")
    if len(parts) >= 5 and parts[0] == "api" and parts[1] == "playback":
        return parts[2], parts[3]
    return "", session_id


async def collect_export_pieces(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
) -> dict:
    """Gather overlapping playable session pieces for one camera."""
    dates = _local_dates_covering(range_start, range_end)
    by_session: dict[str, dict] = {}
    camera_name = camera_ref
    camera_uid = await resolve_camera_uid(camera_ref) or camera_ref

    for date_str in dates:
        result = await search_recordings_by_date(camera_ref, date_str)
        if result.get("status") == 404:
            continue
        camera_name = result.get("cameraName") or camera_name
        camera_uid = result.get("cameraUid") or camera_uid
        for rec in result.get("recordings") or []:
            if rec.get("playable") is False or rec.get("error"):
                continue
            sid = str(rec.get("sessionId") or "")
            if not sid:
                continue
            # Prefer longest/day-clipped entry; merge by keeping earliest start/latest end
            existing = by_session.get(sid)
            if existing is None:
                by_session[sid] = dict(rec)
            else:
                # Expand window if newer entry has wider bounds
                es = _parse_iso(existing.get("startTime"))
                ee = _parse_iso(existing.get("endTime"))
                ns = _parse_iso(rec.get("startTime"))
                ne = _parse_iso(rec.get("endTime"))
                if ns and (es is None or ns < es):
                    existing["startTime"] = rec["startTime"]
                if ne and (ee is None or ne > ee):
                    existing["endTime"] = rec["endTime"]
                if (rec.get("duration") or 0) > (existing.get("duration") or 0):
                    existing["duration"] = rec.get("duration")
                if rec.get("playlistUrl"):
                    existing["playlistUrl"] = rec["playlistUrl"]
                if rec.get("filePath"):
                    existing["filePath"] = rec["filePath"]

    pieces: list[dict] = []
    for rec in by_session.values():
        piece = _overlap_piece(rec, range_start, range_end)
        if piece:
            pieces.append(piece)
    pieces.sort(key=lambda p: p["clipStart"])
    gaps = compute_gaps(range_start, range_end, pieces)
    return {
        "cameraId": camera_ref,
        "cameraUid": camera_uid,
        "cameraName": camera_name,
        "pieces": pieces,
        "gaps": gaps,
    }


async def _run_ffmpeg(args: list[str], timeout: float = EXPORT_FFMPEG_TIMEOUT_SEC) -> tuple[bool, str]:
    bin_path = ffmpeg_bin()
    cmd = [bin_path, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "ffmpeg timeout"
        stderr = (err or b"").decode("utf-8", errors="ignore")[-2000:]
        if proc.returncode != 0:
            return False, stderr or f"ffmpeg exit {proc.returncode}"
        return True, stderr
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except Exception as exc:
        return False, str(exc)


async def _resolve_export_session_dir(piece: dict) -> Path:
    """Locate session dir for export without exposing arbitrary filesystem paths."""
    root = get_effective_recordings_dir().resolve()
    file_path = str(piece.get("filePath") or "").replace("\\", "/").strip().lstrip("/")
    if file_path and ".." not in file_path.split("/") and "/sessions/" in f"/{file_path}/":
        candidate = (root / file_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise RecordingMediaError("Invalid session path", 400) from exc
        if candidate.is_dir() and (candidate / "index.m3u8").is_file():
            return candidate

    folder, session_id = _folder_and_session_from_rec(piece)
    if not folder or not session_id:
        raise RecordingMediaError("missing session path", 400)
    return await resolve_session_dir(folder, session_id)


async def _remux_piece_to_mp4(
    piece: dict,
    out_path: Path,
) -> tuple[bool, str, str]:
    try:
        session_dir = await _resolve_export_session_dir(piece)
    except RecordingMediaError as e:
        return False, e.message, "none"
    playlist = session_dir / "index.m3u8"
    if not playlist.is_file():
        return False, "playlist missing", "none"

    offset = float(piece.get("offsetSeconds") or 0)
    duration = float(piece.get("durationSeconds") or 0)
    if duration <= 0:
        return False, "empty duration", "none"

    # Input seek then copy remux — avoids re-encode when codecs are MP4-compatible.
    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{offset:.3f}",
        "-i",
        str(playlist),
        "-t",
        f"{duration:.3f}",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    ok, err = await _run_ffmpeg(args)
    if ok and out_path.is_file() and out_path.stat().st_size >= 32:
        return True, "", "copy"
    # Fallback: video-only copy (HLS archive is often video-only).
    args_fb = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{offset:.3f}",
        "-i",
        str(playlist),
        "-t",
        f"{duration:.3f}",
        "-an",
        "-c:v",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    ok, err = await _run_ffmpeg(args_fb)
    if ok and out_path.is_file() and out_path.stat().st_size >= 32:
        return True, "", "copy_video_only"
    # Last resort: light re-encode so offline MP4 is still produced (declared, not silent).
    args_re = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{offset:.3f}",
        "-i",
        str(playlist),
        "-t",
        f"{duration:.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    ok, err = await _run_ffmpeg(args_re)
    if not ok or not out_path.is_file() or out_path.stat().st_size < 32:
        return False, err or "remux failed", "none"
    return True, "", "reencode_h264"


async def _concat_mp4s(parts: list[Path], out_path: Path) -> tuple[bool, str]:
    if not parts:
        return False, "no parts"
    if len(parts) == 1:
        shutil.copy2(parts[0], out_path)
        return True, ""
    list_file = out_path.parent / f"concat_{uuid.uuid4().hex}.txt"
    lines = []
    for p in parts:
        # Escape single quotes for concat demuxer
        escaped = str(p).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(out_path),
        ]
        ok, err = await _run_ffmpeg(args)
        if not ok or not out_path.is_file():
            return False, err or "concat failed"
        return True, ""
    finally:
        try:
            list_file.unlink(missing_ok=True)
        except OSError:
            pass


async def export_camera_interval(
    camera_ref: str,
    range_start: datetime,
    range_end: datetime,
    work_dir: Path,
) -> dict:
    """
    Export one camera's selected interval to an MP4 under work_dir.

    Returns camera result dict (ok / gaps / filename / error). Does not raise for
    no-footage — reports gaps instead.
    """
    collected = await collect_export_pieces(camera_ref, range_start, range_end)
    pieces = collected["pieces"]
    gaps = collected["gaps"]
    uid = collected["cameraUid"]
    name = collected["cameraName"]
    label = _safe_filename(uid or name)

    if not pieces:
        return {
            "cameraId": collected["cameraId"],
            "cameraUid": uid,
            "cameraName": name,
            "ok": False,
            "code": "no_footage",
            "error": "No recording available in selected interval",
            "filename": None,
            "exportedSeconds": 0,
            "gaps": gaps
            or [
                {
                    "start": _utc(range_start).isoformat(),
                    "end": _utc(range_end).isoformat(),
                    "reason": "no_footage",
                }
            ],
            "pieces": 0,
        }

    part_paths: list[Path] = []
    part_errors: list[str] = []
    remux_modes: list[str] = []
    source_session_ids: list[str] = []
    source_folders: dict[str, str] = {}
    for i, piece in enumerate(pieces):
        sid = str(piece.get("sessionId") or "")
        folder, _sid = _folder_and_session_from_rec(piece)
        if sid and sid not in source_session_ids:
            source_session_ids.append(sid)
            if folder:
                source_folders[sid] = folder
        part = work_dir / f"{label}_part{i:02d}.mp4"
        ok, err, mode = await _remux_piece_to_mp4(piece, part)
        if ok:
            part_paths.append(part)
            if mode:
                remux_modes.append(mode)
        else:
            part_errors.append(err)
            gaps.append(
                {
                    "start": piece.get("clipStart"),
                    "end": piece.get("clipEnd"),
                    "reason": "remux_failed",
                    "detail": (err or "")[:240],
                }
            )

    if not part_paths:
        return {
            "cameraId": collected["cameraId"],
            "cameraUid": uid,
            "cameraName": name,
            "ok": False,
            "code": "export_failed",
            "error": part_errors[0] if part_errors else "Export remux failed",
            "filename": None,
            "exportedSeconds": 0,
            "gaps": gaps,
            "pieces": len(pieces),
        }

    out_name = f"{label}.mp4"
    out_path = work_dir / out_name
    ok, err = await _concat_mp4s(part_paths, out_path)
    for p in part_paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    if not ok:
        return {
            "cameraId": collected["cameraId"],
            "cameraUid": uid,
            "cameraName": name,
            "ok": False,
            "code": "export_failed",
            "error": err or "concat failed",
            "filename": None,
            "exportedSeconds": 0,
            "gaps": gaps,
            "pieces": len(pieces),
        }

    exported = sum(float(p.get("durationSeconds") or 0) for p in pieces)
    from app.services.evidence_integrity import export_file_integrity, load_manifest
    from app.services.recording_media import resolve_session_dir

    integrity = export_file_integrity(out_path, role="exported_mp4")
    source_manifests: list[dict] = []
    for sid in source_session_ids:
        folder = source_folders.get(sid) or uid or collected["cameraId"]
        try:
            sdir = await resolve_session_dir(str(folder), sid)
            man = load_manifest(sdir)
            if man:
                source_manifests.append(
                    {
                        "session_id": sid,
                        "manifest_sha256": man.get("manifest_sha256"),
                        "created_at": man.get("created_at"),
                        "file_count": man.get("file_count"),
                        "algorithm": man.get("algorithm"),
                    }
                )
        except Exception:
            continue

    remux_declared = "copy"
    if any(m == "reencode_h264" for m in remux_modes):
        remux_declared = "reencode_h264"
    elif any(m == "copy_video_only" for m in remux_modes):
        remux_declared = "copy_video_only"

    # Subtract remux-failed piece durations already listed in gaps with remux_failed
    return {
        "cameraId": collected["cameraId"],
        "cameraUid": uid,
        "cameraName": name,
        "ok": True,
        "code": None,
        "error": None,
        "filename": out_name,
        "exportedSeconds": round(exported, 3),
        "gaps": gaps,
        "pieces": len(part_paths),
        "remux": remux_declared,
        "remuxModes": remux_modes,
        "integrity": integrity,
        "sourceSessions": source_session_ids,
        "sourceEvidence": source_manifests,
    }


async def build_export_archive(
    camera_refs: list[str],
    range_start: datetime,
    range_end: datetime,
) -> tuple[bytes, dict]:
    """
    Build a ZIP archive of per-camera MP4 clips + report.json.

    Partial success is allowed: cameras with no footage are reported in report.json
    without failing the whole archive (as long as at least one camera succeeded OR
    we still return a report-only zip).
    """
    range_start = _utc(range_start)
    range_end = _utc(range_end)
    if range_end <= range_start:
        raise ValueError("end must be after start")
    span = (range_end - range_start).total_seconds()
    if span > MAX_EXPORT_SECONDS:
        raise ValueError(f"export interval exceeds {MAX_EXPORT_SECONDS} seconds")
    if not camera_refs:
        raise ValueError("at least one camera is required")
    if len(camera_refs) > MAX_EXPORT_CAMERAS:
        raise ValueError(f"at most {MAX_EXPORT_CAMERAS} cameras allowed")

    work = Path(tempfile.mkdtemp(prefix="nvr_export_"))
    try:
        camera_results: list[dict] = []
        for ref in camera_refs:
            cam_dir = work / _safe_filename(ref)
            cam_dir.mkdir(parents=True, exist_ok=True)
            result = await export_camera_interval(ref, range_start, range_end, cam_dir)
            camera_results.append(result)

        report = {
            "timezone": get_effective_app_timezone_name(),
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
            "format": "mp4",
            "remux": "copy",
            "cameras": camera_results,
            "successCount": sum(1 for c in camera_results if c.get("ok")),
            "requestedCount": len(camera_results),
            "evidence": {
                "algorithm": "SHA-256",
                "schema": "rdso_18_3_13_export_integrity_v1",
                "note": (
                    "Each successful camera entry includes integrity.sha256 for the "
                    "exported MP4. Source session evidence manifests are referenced "
                    "when present; verify offline by re-hashing cameras/*.mp4."
                ),
                "files": [
                    {
                        "path": f"cameras/{c['filename']}",
                        **(c.get("integrity") or {}),
                    }
                    for c in camera_results
                    if c.get("ok") and c.get("filename") and c.get("integrity")
                ],
            },
        }
        # Honest overall remux label
        modes = [c.get("remux") for c in camera_results if c.get("ok") and c.get("remux")]
        if any(m == "reencode_h264" for m in modes):
            report["remux"] = "reencode_h264"
        elif any(m == "copy_video_only" for m in modes):
            report["remux"] = "copy_video_only"

        buf_path = work / "export.zip"
        with zipfile.ZipFile(buf_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report.json", json.dumps(report, indent=2))
            zf.writestr(
                "evidence_integrity.json",
                json.dumps(report.get("evidence") or {}, indent=2),
            )
            for c in camera_results:
                if not c.get("ok") or not c.get("filename"):
                    continue
                cam_key = _safe_filename(c.get("cameraUid") or c.get("cameraId") or "camera")
                src = work / cam_key / str(c["filename"])
                if not src.is_file():
                    continue
                zf.write(src, f"cameras/{c['filename']}")

        payload = buf_path.read_bytes()
        return payload, report
    finally:
        shutil.rmtree(work, ignore_errors=True)
