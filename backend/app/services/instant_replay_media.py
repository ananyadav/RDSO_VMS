"""Secure serving of Instant Replay temporary HLS buffer media."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from app.services.instant_replay_buffer import IR_FOLDER, buffer_dir
from app.services.recording_media import (
    RecordingMediaError,
    content_type_for,
    media_headers,
    validate_filename,
)

logger = logging.getLogger(__name__)


def _path_inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def rewrite_ir_buffer_playlist(
    content: str,
    camera_uid: str,
    *,
    auth_query: str = "",
) -> str:
    base = f"/api/playback/instant-replay-buffer/{camera_uid}/media/"
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


async def build_instant_replay_buffer_media_response(
    camera_uid: str,
    filename: str,
    *,
    auth_query: str = "",
) -> web.Response:
    uid = (camera_uid or "").strip()
    if not uid or "/" in uid or "\\" in uid or ".." in uid:
        raise RecordingMediaError("Invalid cameraUid", 400)

    validate_filename(filename)
    session_dir = buffer_dir(uid)
    # Ensure we are under the IR folder (never permanent Recordings sessions).
    if IR_FOLDER not in session_dir.parts:
        raise RecordingMediaError("Forbidden", 403)

    file_path = (session_dir / filename).resolve()
    if not _path_inside(session_dir, file_path):
        raise RecordingMediaError("Forbidden", 403)
    if not file_path.is_file():
        raise RecordingMediaError("Recording file not found", 404)

    content_type = content_type_for(filename)
    headers = media_headers(content_type)

    if filename.lower() == "index.m3u8":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        # Live rolling buffer — do not append ENDLIST.
        body = rewrite_ir_buffer_playlist(raw, uid, auth_query=auth_query)
        return web.Response(text=body, headers=headers)

    return web.FileResponse(path=file_path, headers=headers)
