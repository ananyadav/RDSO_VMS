"""Playback API routes — Phase 2 + Instant Replay."""

import logging
from datetime import datetime, timezone

from aiohttp import web

from app.core.access_control import deny_unless_camera_access, deny_unless_playback_permission
from app.core.auth_context import get_effective_user
from app.services.app_timezone import get_effective_app_timezone_name
from app.services.audit_service import ACTION_RECORDING_EXPORT_CREATED, write_audit
from app.services.camera_identity import get_camera_by_ref, resolve_camera_uid
from app.services.instant_replay_config import INSTANT_REPLAY_ENABLED, instant_replay_public_config
from app.services.instant_replay_media import build_instant_replay_buffer_media_response
from app.services.instant_replay_resolve import instant_replay_availability, resolve_instant_replay
from app.services.playback_search import (
    MAX_MULTI_PLAYBACK_CAMERAS,
    get_recording_dates_for_month,
    parse_multi_at_iso,
    search_recordings_by_date,
    search_recordings_multi,
)
from app.services.recording_export import MAX_EXPORT_CAMERAS, build_export_archive
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


async def _parse_json_body(request: web.Request) -> dict:
    try:
        if request.can_read_body:
            data = await request.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


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


async def playback_multi_search_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/multi-search?date=YYYY-MM-DD&cameraUid=a&cameraUid=b&at=ISO8601

    Also accepts comma-separated cameraUids / cameraIds query params.
    Unauthorized cameras are omitted (not exposed). Requires recording.view.
    """
    date_str = request.rel_url.query.get("date", "").strip()
    if not date_str:
        return web.json_response({"error": "date is required (YYYY-MM-DD)"}, status=400)

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return web.json_response({"error": "date must be YYYY-MM-DD"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    refs: list[str] = []
    for key in ("cameraUid", "cameraId", "cameraUids", "cameraIds"):
        for raw in request.rel_url.query.getall(key, []):
            for part in str(raw).split(","):
                token = part.strip()
                if token and token not in refs:
                    refs.append(token)

    if not refs:
        return web.json_response(
            {"error": "at least one cameraUid or cameraId is required"},
            status=400,
        )
    if len(refs) > MAX_MULTI_PLAYBACK_CAMERAS:
        return web.json_response(
            {
                "error": f"at most {MAX_MULTI_PLAYBACK_CAMERAS} cameras allowed",
            },
            status=400,
        )

    at_raw = (request.rel_url.query.get("at") or "").strip() or None
    try:
        at_dt = parse_multi_at_iso(at_raw)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    allowed_refs: list[str] = []
    for ref in refs:
        cam_denied = await deny_unless_camera_access(request, ref)
        if cam_denied is not None:
            # Do not expose unauthorized / unknown cameras in the payload.
            continue
        allowed_refs.append(ref)

    try:
        result = await search_recordings_multi(allowed_refs, date_str, at=at_dt)
    except Exception as e:
        logger.error(f"[PLAYBACK] multi-search failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

    result["requestedCount"] = len(refs)
    result["authorizedCount"] = len(allowed_refs)
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


async def playback_client_config_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/client-config

    Authenticated read-only client settings for Playback UI (no secrets).
    """
    user = await get_effective_user(request)
    if user is None:
        return web.json_response({"error": "Authentication required"}, status=401)
    return web.json_response(
        {
            "timezone": get_effective_app_timezone_name(),
            "instantReplay": instant_replay_public_config(),
        }
    )


async def instant_replay_resolve_endpoint(request: web.Request) -> web.Response:
    """
    GET|POST /api/playback/instant-replay/resolve
    Query/body: cameraUid|cameraId, secondsAgo? | at?
    """
    camera_ref = _camera_ref_from_request(request)
    q = request.rel_url.query
    seconds_ago_raw = q.get("secondsAgo")
    at_iso = (q.get("at") or "").strip() or None

    if request.method == "POST":
        body = await _parse_json_body(request)
        if not camera_ref:
            camera_ref = (body.get("cameraUid") or body.get("cameraId") or "").strip()
        if seconds_ago_raw is None and body.get("secondsAgo") is not None:
            seconds_ago_raw = body.get("secondsAgo")
        if not at_iso:
            at_iso = (body.get("at") or "").strip() or None

    if not camera_ref:
        return web.json_response({"error": "cameraUid or cameraId is required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    seconds_ago = None
    if seconds_ago_raw is not None and str(seconds_ago_raw).strip() != "":
        try:
            seconds_ago = float(seconds_ago_raw)
        except (TypeError, ValueError):
            return web.json_response({"error": "Invalid secondsAgo"}, status=400)

    try:
        result = await resolve_instant_replay(
            camera_ref, seconds_ago=seconds_ago, at_iso=at_iso
        )
    except Exception as e:
        logger.error("[INSTANT-REPLAY] resolve failed: %s", e, exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

    if result.get("code") == "bad_request":
        return web.json_response(result, status=400)
    # no_footage still 200 with ok:false so UI can show a clear message
    return web.json_response(result)


async def instant_replay_availability_endpoint(request: web.Request) -> web.Response:
    """GET /api/playback/instant-replay/availability?cameraUid=..."""
    camera_ref = _camera_ref_from_request(request)
    if not camera_ref:
        return web.json_response({"error": "cameraUid or cameraId is required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    try:
        result = await instant_replay_availability(camera_ref)
    except Exception as e:
        logger.error("[INSTANT-REPLAY] availability failed: %s", e, exc_info=True)
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response(result)


async def instant_replay_lease_acquire_endpoint(request: web.Request) -> web.Response:
    """
    POST /api/playback/instant-replay/lease
    Body: { cameraUid|cameraId, leaseId? }

    Starts at most one rolling short-segment IR buffer producer per camera while
    Live View / Instant Replay consumers hold leases — including when permanent
    recording is already active (permanent FFmpeg is separate; IR remux is extra).
    """
    from app.services.instant_replay_buffer import acquire_buffer_lease
    from app.services.video_recording import is_camera_recording

    body = await _parse_json_body(request)
    camera_ref = (
        (body.get("cameraUid") or body.get("cameraId") or _camera_ref_from_request(request) or "")
        .strip()
    )
    lease_id = (body.get("leaseId") or "").strip() or None

    if not camera_ref:
        return web.json_response({"error": "cameraUid or cameraId is required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    if not INSTANT_REPLAY_ENABLED:
        return web.json_response(
            {"ok": False, "enabled": False, "error": "Instant Replay disabled"},
            status=200,
        )

    cam = await get_camera_by_ref(camera_ref)
    if not cam:
        return web.json_response({"error": "Camera not found"}, status=404)

    uid = await resolve_camera_uid(camera_ref) or camera_ref
    camera_id = str(cam["_id"])

    recording_active = False
    try:
        recording_active = await is_camera_recording(camera_id)
    except Exception:
        recording_active = False

    result = await acquire_buffer_lease(uid, cam, lease_id)
    result["bufferStarted"] = bool(result.get("ok"))
    result["permanentRecordingActive"] = recording_active
    result["config"] = instant_replay_public_config()
    status = 200 if result.get("ok") else 500
    return web.json_response(result, status=status)


async def instant_replay_lease_heartbeat_endpoint(request: web.Request) -> web.Response:
    """POST /api/playback/instant-replay/lease/heartbeat"""
    from app.services.instant_replay_buffer import heartbeat_buffer_lease

    body = await _parse_json_body(request)
    camera_ref = (body.get("cameraUid") or body.get("cameraId") or "").strip()
    lease_id = (body.get("leaseId") or "").strip()
    if not camera_ref or not lease_id:
        return web.json_response({"error": "cameraUid and leaseId are required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    uid = await resolve_camera_uid(camera_ref) or camera_ref
    result = await heartbeat_buffer_lease(uid, lease_id)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def instant_replay_lease_release_endpoint(request: web.Request) -> web.Response:
    """POST /api/playback/instant-replay/lease/release"""
    from app.services.instant_replay_buffer import release_buffer_lease

    body = await _parse_json_body(request)
    camera_ref = (body.get("cameraUid") or body.get("cameraId") or "").strip()
    lease_id = (body.get("leaseId") or "").strip()
    if not camera_ref or not lease_id:
        return web.json_response({"error": "cameraUid and leaseId are required"}, status=400)

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_ref)
    if denied is not None:
        return denied

    uid = await resolve_camera_uid(camera_ref) or camera_ref
    result = await release_buffer_lease(uid, lease_id)
    return web.json_response(result)


async def instant_replay_buffer_media_endpoint(request: web.Request) -> web.Response:
    """
    GET /api/playback/instant-replay-buffer/{cameraUid}/media/{filename}

    Temporary IR HLS — requires recording.view + camera ACL.
    cameraUid in path must match the buffer folder (no cross-camera).
    """
    camera_uid = request.match_info.get("cameraUid", "").strip()
    filename = request.match_info.get("filename", "").strip()

    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    denied = await deny_unless_camera_access(request, camera_uid)
    if denied is not None:
        return denied

    # Buffer dirs are keyed by stable camera_uid (ip_*).
    resolved = await resolve_camera_uid(camera_uid)
    camera_uid_for_fs = resolved or camera_uid

    uid_param = (request.query.get("uid") or request.query.get("userId") or "").strip()
    auth_query = f"uid={uid_param}" if uid_param else ""

    try:
        return await build_instant_replay_buffer_media_response(
            camera_uid_for_fs, filename, auth_query=auth_query
        )
    except RecordingMediaError as e:
        logger.warning(
            "[INSTANT-REPLAY] Buffer media error: camera=%s file=%s status=%s message=%s",
            camera_uid,
            filename,
            e.status,
            e.message,
        )
        return media_error_response(e)
    except Exception as e:
        logger.error(
            "[INSTANT-REPLAY] Buffer media failed: camera=%s file=%s error=%s",
            camera_uid,
            filename,
            e,
            exc_info=True,
        )
        return web.Response(status=500, text="Internal server error")


async def playback_export_endpoint(request: web.Request) -> web.Response:
    """
    POST /api/playback/export
    Body JSON: {
      cameraUids?: string[], cameraIds?: string[],
      start: ISO-8601, end: ISO-8601
    }

    Exports the selected interval as offline MP4 (stream copy when safe),
    packaged in a ZIP (cameras/*.mp4 + report.json). Partial gaps do not fail
    the whole job. Requires recording.view + per-camera ACL.
    """
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied

    body = await _parse_json_body(request)
    refs: list[str] = []
    for key in ("cameraUids", "cameraIds", "cameras"):
        raw = body.get(key)
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip()
                if token and token not in refs:
                    refs.append(token)
        elif isinstance(raw, str):
            for part in raw.split(","):
                token = part.strip()
                if token and token not in refs:
                    refs.append(token)
    single = (body.get("cameraUid") or body.get("cameraId") or "").strip()
    if single and single not in refs:
        refs.insert(0, single)

    if not refs:
        return web.json_response({"error": "at least one cameraUid/cameraId is required"}, status=400)
    if len(refs) > MAX_EXPORT_CAMERAS:
        return web.json_response(
            {"error": f"at most {MAX_EXPORT_CAMERAS} cameras allowed"},
            status=400,
        )

    start_raw = (body.get("start") or "").strip()
    end_raw = (body.get("end") or "").strip()
    if not start_raw or not end_raw:
        return web.json_response({"error": "start and end are required (ISO-8601)"}, status=400)
    try:
        start_dt = parse_multi_at_iso(start_raw)
        end_dt = parse_multi_at_iso(end_raw)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if start_dt is None or end_dt is None:
        return web.json_response({"error": "start and end must be ISO-8601 datetimes"}, status=400)

    allowed: list[str] = []
    for ref in refs:
        cam_denied = await deny_unless_camera_access(request, ref)
        if cam_denied is not None:
            continue
        allowed.append(ref)

    if not allowed:
        return web.json_response({"error": "No authorized cameras in selection"}, status=403)

    try:
        payload, report = await build_export_archive(allowed, start_dt, end_dt)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.error("[PLAYBACK] export failed: %s", exc, exc_info=True)
        return web.json_response({"error": "Export failed"}, status=500)

    actor = await get_effective_user(request)
    await write_audit(
        action=ACTION_RECORDING_EXPORT_CREATED,
        actor=actor,
        resource_type="recording_export",
        resource_id=None,
        resource_label=f"clip-{report.get('start')}-{report.get('end')}",
        request=request,
        success=True,
        metadata={
            "cameras": [c.get("cameraUid") for c in report.get("cameras") or []],
            "successCount": report.get("successCount"),
            "requestedCount": report.get("requestedCount"),
            "evidence_files": len((report.get("evidence") or {}).get("files") or []),
            "remux": report.get("remux"),
            "authorizedCount": len(allowed),
            "bytes": len(payload),
            "format": "mp4/zip",
        },
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"playback-export-{stamp}.zip"
    return web.Response(
        body=payload,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Success-Count": str(report.get("successCount") or 0),
            "X-Export-Requested-Count": str(report.get("requestedCount") or 0),
        },
    )


def setup_playback_routes(app: web.Application) -> None:
    app.router.add_get("/api/playback/client-config", playback_client_config_endpoint)
    app.router.add_get("/api/playback/search", playback_search_endpoint)
    app.router.add_get("/api/playback/multi-search", playback_multi_search_endpoint)
    app.router.add_post("/api/playback/export", playback_export_endpoint)
    app.router.add_get("/api/playback/dates", playback_dates_endpoint)

    # Instant Replay (register before parameterized media routes)
    app.router.add_get(
        "/api/playback/instant-replay/resolve", instant_replay_resolve_endpoint
    )
    app.router.add_post(
        "/api/playback/instant-replay/resolve", instant_replay_resolve_endpoint
    )
    app.router.add_get(
        "/api/playback/instant-replay/availability",
        instant_replay_availability_endpoint,
    )
    app.router.add_post(
        "/api/playback/instant-replay/lease", instant_replay_lease_acquire_endpoint
    )
    app.router.add_post(
        "/api/playback/instant-replay/lease/heartbeat",
        instant_replay_lease_heartbeat_endpoint,
    )
    app.router.add_post(
        "/api/playback/instant-replay/lease/release",
        instant_replay_lease_release_endpoint,
    )
    app.router.add_get(
        "/api/playback/instant-replay-buffer/{cameraUid}/media/{filename}",
        instant_replay_buffer_media_endpoint,
    )

    app.router.add_get(
        "/api/playback/{cameraId}/{sessionId}/media/{filename}",
        playback_media_endpoint,
    )
