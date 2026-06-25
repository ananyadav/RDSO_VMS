"""Playback API routes — Phase 2."""

import logging
from datetime import datetime

from aiohttp import web

from app.core.access_control import deny_unless_camera_access, deny_unless_playback_permission
from app.services.playback_search import get_recording_dates_for_month, search_recordings_by_date
from app.services.recording_media import (
    RecordingMediaError,
    build_recording_media_response,
    media_error_response,
)

logger = logging.getLogger(__name__)


def _camera_ref_from_request(request) -> str:
    q = request.rel_url.query
    uid = (q.get("cameraUid") or "").strip()
    cid = (q.get("cameraId") or "").strip()
    return uid or cid


async def playback_search_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/search?cameraUid=<uid>&date=YYYY-MM-DD
    or ?cameraId=<mongoId>&date=YYYY-MM-DD
    """
    camera_ref = _camera_ref_from_request(request)
    date_str = request.rel_url.query.get("date", "").strip()

    if not camera_ref:
        return web.json_response({"error": "cameraUid or cameraId is required"}, status=400)
    if not date_str:
        return web.json_response({"error": "date is required (YYYY-MM-DD)"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return web.json_response(
            {"error": "date must be YYYY-MM-DD"},
            status=400,
        )

    try:
        result = await search_recordings_by_date(camera_ref, date_str)
    except Exception as e:
        logger.error(f"[PLAYBACK] search failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

    if result.get("status") == 404:
        return web.json_response({"error": result["error"]}, status=404)

    return web.json_response(result)


async def playback_dates_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/dates?cameraUid=<uid>&year=YYYY&month=M
    """
    camera_ref = _camera_ref_from_request(request)
    year_str = request.rel_url.query.get("year", "").strip()
    month_str = request.rel_url.query.get("month", "").strip()

    if not camera_ref:
        return web.json_response({"error": "cameraUid or cameraId is required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    try:
        year = int(year_str)
        month = int(month_str)
    except ValueError:
        return web.json_response({"error": "year and month are required integers"}, status=400)

    try:
        result = await get_recording_dates_for_month(camera_ref, year, month)
    except Exception as e:
        logger.error(f"[PLAYBACK] dates failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

    if result.get("status") == 404:
        return web.json_response({"error": result["error"]}, status=404)
    if result.get("status") == 400:
        return web.json_response({"error": result["error"]}, status=400)

    return web.json_response(result)


async def playback_media_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/{cameraId}/{sessionId}/media/{filename}

    Securely serve recorded HLS playlist (index.m3u8) and segments (.ts).
    """
    camera_id = request.match_info.get("cameraId", "").strip()
    session_id = request.match_info.get("sessionId", "").strip()
    filename = request.match_info.get("filename", "").strip()

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied

    uid = (request.query.get("uid") or request.query.get("userId") or "").strip()
    auth_query = f"uid={uid}" if uid else ""

    try:
        return await build_recording_media_response(
            camera_id, session_id, filename, auth_query=auth_query
        )
    except RecordingMediaError as e:
        logger.warning(
            "[PLAYBACK] Media error: camera=%s session=%s file=%s status=%s message=%s",
            camera_id,
            session_id,
            filename,
            e.status,
            e.message,
        )
        return media_error_response(e)
    except Exception as e:
        logger.error(
            "[PLAYBACK] Media serve failed: camera=%s session=%s file=%s error=%s",
            camera_id,
            session_id,
            filename,
            e,
            exc_info=True,
        )
        return web.Response(status=500, text="Internal server error")


def setup_playback_routes(app: web.Application) -> None:
    app.router.add_get("/api/playback/search", playback_search_endpoint)
    app.router.add_get("/api/playback/dates", playback_dates_endpoint)
    app.router.add_get(
        "/api/playback/{cameraId}/{sessionId}/media/{filename}",
        playback_media_endpoint,
    )
