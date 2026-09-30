"""Secure serving of recorded HLS playlists (.m3u8) and segments (.ts)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Set

from aiohttp import web
from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection, recording_sessions_collection
from app.services.camera_identity import resolve_camera_uid, storage_folder_keys_for_uid
from app.services.camera_uid import ip_from_camera_uid, make_camera_uid
from app.services.video_recording import (
    ACTIVE_RECORDINGS,
    session_storage_dir,
    storage_folder_from_path,
)

logger = logging.getLogger(__name__)

RECORDING_FILE_NOT_FOUND = "Recording file not found"
RECORDING_SESSION_NOT_FOUND = "Recording session not found"

_ALLOWED_PLAYLISTS = frozenset({"index.m3u8"})
_SEGMENT_NAME = re.compile(r"^seg_\d+\.ts$", re.IGNORECASE)
_TS_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.ts$")


class RecordingMediaError(Exception):
    def __init__(self, message: str, status: int = 404):
        self.message = message
        self.status = status
        super().__init__(message)


def _is_object_id(value: str) -> bool:
    try:
        ObjectId(str(value))
        return True
    except (InvalidId, TypeError):
        return False


def _path_inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def validate_filename(filename: str) -> None:
    """Allow playlist, segments, and sealed evidence manifests — block traversal."""
    if not filename or filename in (".", ".."):
        raise RecordingMediaError("Invalid filename", 400)
    if "/" in filename or "\\" in filename:
        raise RecordingMediaError("Invalid filename", 400)

    lower = filename.lower()
    if lower in _ALLOWED_PLAYLISTS:
        return
    if lower in ("evidence_manifest.json", "evidence_integrity.json"):
        return
    if _SEGMENT_NAME.match(filename) or _TS_NAME.match(filename):
        return
    raise RecordingMediaError("File type not allowed", 403)


async def validate_camera_media_ref(camera_ref: str) -> None:
    """Accept MongoDB camera id or stable camera_uid folder (ip_*)."""
    ref = (camera_ref or "").strip()
    if not ref:
        raise RecordingMediaError("Invalid cameraId", 400)
    if ref.startswith("ip_"):
        return
    if _is_object_id(ref):
        doc = await camera_collection.find_one({"_id": ObjectId(ref)})
        if not doc:
            raise RecordingMediaError("Camera not found", 404)
        return
    raise RecordingMediaError("Invalid cameraId", 400)


async def _load_session_identity(session_id: str) -> Optional[dict]:
    """Raw session identity fields for ownership checks (includes legacy keys)."""
    if not session_id or not _is_object_id(session_id):
        return None
    return await recording_sessions_collection.find_one(
        {"_id": ObjectId(session_id)},
        {"camera_id": 1, "camera_uid": 1, "ip_address": 1, "storage_path": 1},
    )


def _session_identity_keys(session: dict) -> Set[str]:
    keys: Set[str] = set()
    for field in ("camera_uid", "camera_id", "ip_address"):
        value = str(session.get(field) or "").strip()
        if value:
            keys.add(value)
    folder = storage_folder_from_path(session.get("storage_path"), "")
    if folder:
        keys.add(folder)
    return keys


async def _allowed_camera_identity_keys(camera_ref: str) -> Set[str]:
    """All camera identifiers that may own recordings for the URL cameraId."""
    ref = (camera_ref or "").strip()
    keys: Set[str] = set()
    if not ref:
        return keys
    keys.add(ref)

    uid = await resolve_camera_uid(ref)
    if uid:
        keys.add(uid)
        ip = ip_from_camera_uid(uid)
        if ip:
            keys.add(ip)
        for folder in await storage_folder_keys_for_uid(uid):
            if folder:
                keys.add(str(folder).strip())
    elif _is_object_id(ref):
        doc = await camera_collection.find_one({"_id": ObjectId(ref)})
        if doc:
            mongo_id = str(doc["_id"])
            keys.add(mongo_id)
            cam_uid = (doc.get("camera_uid") or make_camera_uid(doc.get("ip_address") or "") or "").strip()
            if cam_uid:
                keys.add(cam_uid)
                ip = ip_from_camera_uid(cam_uid)
                if ip:
                    keys.add(ip)
            stored = doc.get("recording_storage_id")
            if stored:
                keys.add(str(stored).strip())

    return {k for k in keys if k}


async def assert_session_belongs_to_camera(camera_id: str, session: dict) -> None:
    """
    Require Mongo session identity to match the URL cameraId.

    Mismatch returns 404 (not 403) so foreign session existence is not disclosed.
    """
    allowed = await _allowed_camera_identity_keys(camera_id)
    session_keys = _session_identity_keys(session)
    if not allowed or not session_keys or session_keys.isdisjoint(allowed):
        logger.warning(
            "[PLAYBACK] Session ownership mismatch: camera=%s session=%s",
            camera_id,
            session.get("_id"),
        )
        raise RecordingMediaError(RECORDING_SESSION_NOT_FOUND, 404)


async def resolve_session_dir(camera_id: str, session_id: str) -> Path:
    if not session_id or not _is_object_id(session_id):
        raise RecordingMediaError("Invalid sessionId", 400)

    session = await _load_session_identity(session_id)
    if session is not None:
        await assert_session_belongs_to_camera(camera_id, session)

    # Only search folders owned by the requested camera — never foreign session paths.
    allowed_folders = await _allowed_camera_identity_keys(camera_id)
    candidates: list[Path] = []
    seen: set[str] = set()

    def add_folder(folder: str) -> None:
        folder = (folder or "").strip()
        if not folder or folder in seen:
            return
        seen.add(folder)
        candidates.append(session_storage_dir(folder, session_id))

    for folder in allowed_folders:
        add_folder(folder)

    for session_dir in candidates:
        if session_dir.is_dir():
            return session_dir

    if session is not None:
        logger.warning(
            "[PLAYBACK] Recording file not found: camera=%s session=%s "
            "(MongoDB metadata exists, session folder missing)",
            camera_id,
            session_id,
        )
        raise RecordingMediaError(RECORDING_FILE_NOT_FOUND, 404)

    logger.warning(
        "[PLAYBACK] Recording session not found: camera=%s session=%s",
        camera_id,
        session_id,
    )
    raise RecordingMediaError(RECORDING_SESSION_NOT_FOUND, 404)


async def resolve_recording_file(camera_id: str, session_id: str, filename: str) -> Path:
    """Validate ids and return a safe file path under the session folder."""
    await validate_camera_media_ref(camera_id)
    session_dir = await resolve_session_dir(camera_id, session_id)
    validate_filename(filename)

    file_path = (session_dir / filename).resolve()
    if not _path_inside(session_dir, file_path):
        raise RecordingMediaError("Forbidden", 403)
    if not file_path.is_file():
        logger.warning(
            "[PLAYBACK] Recording file not found: camera=%s session=%s file=%s",
            camera_id,
            session_id,
            filename,
        )
        raise RecordingMediaError(RECORDING_FILE_NOT_FOUND, 404)
    return file_path


def content_type_for(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if lower.endswith(".ts"):
        return "video/mp2t"
    return "application/octet-stream"


def media_headers(content_type: str) -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Cache-Control": "no-cache, no-store",
        "Access-Control-Allow-Origin": "*",
        "Accept-Ranges": "bytes",
    }


def playlist_media_base(camera_id: str, session_id: str) -> str:
    return f"/api/playback/{camera_id}/{session_id}/media/"


def _session_is_live(_camera_id: str, session_id: str) -> bool:
    for entry in ACTIVE_RECORDINGS.values():
        if entry.get("session_id") == session_id:
            rec = entry["recorder"]
            if getattr(rec, "is_recording", False):
                return True
    return False


def finalize_vod_playlist(content: str, *, live: bool) -> str:
    """Append #EXT-X-ENDLIST for completed sessions so browsers treat the manifest as VOD."""
    if live:
        return content
    stripped = content.rstrip()
    if not stripped or stripped.endswith("#EXT-X-ENDLIST"):
        return content
    text = stripped + "\n#EXT-X-ENDLIST\n"
    return text


def rewrite_playlist_urls(
    content: str,
    camera_id: str,
    session_id: str,
    *,
    auth_query: str = "",
) -> str:
    """
    Rewrite relative segment URIs so the browser requests go through the secure
    playback media route (sessionId stays in the path, not lost on segment fetch).
    """
    base = playlist_media_base(camera_id, session_id)
    suffix = f"?{auth_query}" if auth_query else ""
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and not stripped.startswith("http://")
            and not stripped.startswith("https://")
        ):
            segment_name = stripped.split("?", 1)[0].split("/", 1)[-1]
            if segment_name.lower().endswith((".ts", ".m3u8")):
                try:
                    validate_filename(segment_name)
                    out.append(f"{base}{segment_name}{suffix}")
                    continue
                except RecordingMediaError:
                    pass
        out.append(line)
    text = "\n".join(out)
    if content.endswith("\n"):
        text += "\n"
    return text


async def build_recording_media_response(
    camera_id: str,
    session_id: str,
    filename: str,
    *,
    auth_query: str = "",
) -> web.Response:
    file_path = await resolve_recording_file(camera_id, session_id, filename)
    content_type = content_type_for(filename)
    headers = media_headers(content_type)

    if filename.lower() == "index.m3u8":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        body = rewrite_playlist_urls(raw, camera_id, session_id, auth_query=auth_query)
        body = finalize_vod_playlist(body, live=_session_is_live(camera_id, session_id))
        return web.Response(text=body, headers=headers)

    return web.FileResponse(path=file_path, headers=headers)


def media_error_response(exc: RecordingMediaError) -> web.Response:
    return web.Response(status=exc.status, text=exc.message)
