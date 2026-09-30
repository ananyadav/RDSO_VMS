"""Secure serving of on-demand remote-transcode HLS media (RDSO 18.5(ii))."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from app.services.recording_media import (
    RecordingMediaError,
    content_type_for,
    media_headers,
    validate_filename,
)
from app.services.remote_transcode_service import get_job, remote_transcode_root


def _path_inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def rewrite_remote_transcode_playlist(
    content: str,
    job_id: str,
    *,
    auth_query: str = "",
) -> str:
    base = f"/api/remote-transcode/sessions/{job_id}/media/"
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


async def build_remote_transcode_media_response(
    job_id: str,
    filename: str,
    *,
    user_id: str,
    auth_query: str = "",
) -> web.Response:
    jid = (job_id or "").strip()
    if not jid or "/" in jid or "\\" in jid or ".." in jid:
        raise RecordingMediaError("Invalid job id", 400)

    job = get_job(jid)
    if not job:
        raise RecordingMediaError("Transcode session not found", 404)
    if job.user_id != user_id:
        raise RecordingMediaError("Forbidden", 403)

    job.touch()
    validate_filename(filename)

    session_dir = job.output_dir
    root = remote_transcode_root()
    if not _path_inside(root, session_dir):
        raise RecordingMediaError("Forbidden", 403)

    file_path = (session_dir / filename).resolve()
    if not _path_inside(session_dir, file_path):
        raise RecordingMediaError("Forbidden", 403)
    if not file_path.is_file():
        raise RecordingMediaError("Transcoded media not ready", 404)

    content_type = content_type_for(filename)
    headers = media_headers(content_type)

    if filename.lower() == "index.m3u8":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        body = rewrite_remote_transcode_playlist(raw, jid, auth_query=auth_query)
        return web.Response(text=body, headers=headers)

    return web.FileResponse(path=file_path, headers=headers)
