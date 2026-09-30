"""RDSO 18.5(i)/(ii) — remote web capacity + on-demand transcoding routes."""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiohttp import web

from app.core.access_control import (
    deny_unless_admin_or_live_view,
    deny_unless_camera_access,
    deny_unless_playback_permission,
    require_user,
)
from app.services.camera_identity import get_camera_by_ref
from app.services.camera_uid import make_camera_uid
from app.services.recording_media import RecordingMediaError
from app.services.remote_transcode_profiles import resolve_transcode_intent
from app.services.remote_transcode_media import build_remote_transcode_media_response
from app.services.remote_transcode_service import (
    capability_with_runtime,
    cleanup_idle_jobs,
    get_job,
    heartbeat_job,
    list_jobs_for_user,
    resolve_live_input_url,
    resolve_playback_input_path,
    start_transcode_job,
    stop_transcode_job,
)
from app.services.remote_web_capacity import get_rdso_18_5_i_capacity

logger = logging.getLogger(__name__)


def _user_id(user: dict) -> str:
    return str(user.get("id") or user.get("_id") or "")


async def remote_web_capacity_endpoint(request: web.Request) -> web.Response:
    """GET /api/system/remote-web-capacity — RDSO 18.5(i) evidence."""
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    payload = await get_rdso_18_5_i_capacity(include_counts=True)
    return web.json_response(payload)


async def remote_transcode_capability_endpoint(request: web.Request) -> web.Response:
    """GET /api/remote-transcode/capability — RDSO 18.5(ii)."""
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    await cleanup_idle_jobs()
    return web.json_response(capability_with_runtime())


async def remote_transcode_start_endpoint(request: web.Request) -> web.Response:
    """POST /api/remote-transcode/sessions — start on-demand remote transcode."""
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    kind = str(body.get("kind") or "live").strip().lower()
    if kind not in ("live", "playback"):
        return web.json_response({"error": "kind must be live or playback", "ok": False}, status=400)

    camera_ref = str(body.get("camera_id") or body.get("cameraId") or "").strip()
    if not camera_ref:
        return web.json_response({"error": "camera_id required", "ok": False}, status=400)

    if kind == "live":
        denied = await deny_unless_admin_or_live_view(request)
        if denied is not None:
            return denied
    else:
        denied = await deny_unless_playback_permission(request)
        if denied is not None:
            return denied

    cam_denied = await deny_unless_camera_access(request, camera_ref)
    if cam_denied is not None:
        return cam_denied

    cam = await get_camera_by_ref(camera_ref)
    if not cam:
        return web.json_response({"error": "Camera not found", "ok": False}, status=404)

    camera_id = str(cam.get("_id") or camera_ref)
    camera_uid = str(cam.get("camera_uid") or cam.get("cameraUid") or "").strip()
    if not camera_uid:
        ip = (cam.get("ip_address") or "").strip()
        camera_uid = make_camera_uid(ip) if ip else camera_id
    worker_id = cam.get("worker_id", cam.get("workerId"))
    try:
        worker_i = int(worker_id) if worker_id not in (None, "") else None
    except (TypeError, ValueError):
        worker_i = None

    mode = str(body.get("mode") or "auto").strip().lower()
    bandwidth = body.get("bandwidth_kbps", body.get("bandwidthKbps"))
    profile_id = body.get("profile") or body.get("profile_id")
    intent = resolve_transcode_intent(
        mode=mode,
        bandwidth_kbps=bandwidth,
        profile_id=str(profile_id) if profile_id else None,
    )
    if intent.get("error"):
        return web.json_response({"ok": False, "error": intent["error"], **intent}, status=400)

    if not intent.get("transcode"):
        # Honest: use unchanged LAN/direct path — do not start fleet FFmpeg.
        return web.json_response(
            {
                "ok": True,
                "transcode": False,
                "recommend_direct": True,
                "reason": intent.get("reason"),
                "mode": intent.get("mode"),
                "live_direct": {
                    "path": "go2rtc",
                    "notes": "Use existing Nginx → go2rtc Live View / archived Playback media.",
                },
                "rdso_18_5_ii": True,
            }
        )

    profile = intent["profile"]
    uid = _user_id(user)

    try:
        if kind == "live":
            stream = str(body.get("stream") or "sub").strip().lower()
            if stream not in ("main", "sub"):
                stream = "sub"
            input_url = resolve_live_input_url(camera_uid, stream=stream, worker_id=worker_i)
            # Guard: never pass camera password URLs
            if "@" in input_url.split("://", 1)[-1].split("/", 1)[0]:
                return web.json_response(
                    {
                        "ok": False,
                        "error": "Refusing live input that embeds credentials; go2rtc local RTSP required",
                    },
                    status=500,
                )
            job = await start_transcode_job(
                kind="live",
                camera_id=camera_id,
                camera_uid=camera_uid,
                user_id=uid,
                profile=profile,
                input_url=input_url,
                mode=str(intent.get("mode") or mode),
                bandwidth_kbps=intent.get("bandwidth_kbps"),
            )
        else:
            rec_sid = str(
                body.get("recording_session_id")
                or body.get("session_id")
                or body.get("sessionId")
                or ""
            ).strip()
            if not rec_sid:
                return web.json_response(
                    {"ok": False, "error": "recording_session_id required for playback transcode"},
                    status=400,
                )
            media_path = await resolve_playback_input_path(camera_ref, rec_sid)
            job = await start_transcode_job(
                kind="playback",
                camera_id=camera_id,
                camera_uid=camera_uid,
                user_id=uid,
                profile=profile,
                input_url=str(media_path),
                mode=str(intent.get("mode") or mode),
                bandwidth_kbps=intent.get("bandwidth_kbps"),
                recording_session_id=rec_sid,
            )
    except FileNotFoundError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    except RuntimeError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=503)
    except Exception as exc:
        logger.exception("[remote-transcode] start failed")
        return web.json_response({"ok": False, "error": f"Transcode start failed: {exc}"}, status=500)

    return web.json_response(
        {
            "ok": True,
            "transcode": True,
            "reason": intent.get("reason"),
            "session": job.public(),
            "rdso_18_5_ii": True,
        }
    )


async def remote_transcode_get_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    job_id = request.match_info.get("jobId", "")
    job = get_job(job_id)
    if not job or job.user_id != _user_id(user):
        return web.json_response({"error": "Not found"}, status=404)
    job.touch()
    return web.json_response({"ok": True, "session": job.public()})


async def remote_transcode_list_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    await cleanup_idle_jobs()
    jobs = [j.public() for j in list_jobs_for_user(_user_id(user))]
    return web.json_response({"ok": True, "items": jobs, "total": len(jobs)})


async def remote_transcode_stop_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    job_id = request.match_info.get("jobId", "")
    job = get_job(job_id)
    if not job or job.user_id != _user_id(user):
        return web.json_response({"error": "Not found"}, status=404)
    await stop_transcode_job(job_id, reason="client_stop")
    return web.json_response({"ok": True, "stopped": True})


async def remote_transcode_heartbeat_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    job_id = request.match_info.get("jobId", "")
    job = await heartbeat_job(job_id, user_id=_user_id(user))
    if not job:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"ok": True, "session": job.public()})


async def remote_transcode_media_endpoint(request: web.Request) -> web.Response:
    try:
        user = await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    job_id = request.match_info.get("jobId", "")
    filename = request.match_info.get("filename", "")
    auth_query = request.query_string or ""
    try:
        return await build_remote_transcode_media_response(
            job_id,
            filename,
            user_id=_user_id(user),
            auth_query=auth_query,
        )
    except RecordingMediaError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)


def setup_remote_web_routes(app: web.Application) -> None:
    app.router.add_get("/api/system/remote-web-capacity", remote_web_capacity_endpoint)
    app.router.add_get("/api/remote-transcode/capability", remote_transcode_capability_endpoint)
    app.router.add_get("/api/remote-transcode/sessions", remote_transcode_list_endpoint)
    app.router.add_post("/api/remote-transcode/sessions", remote_transcode_start_endpoint)
    app.router.add_get("/api/remote-transcode/sessions/{jobId}", remote_transcode_get_endpoint)
    app.router.add_delete("/api/remote-transcode/sessions/{jobId}", remote_transcode_stop_endpoint)
    app.router.add_post(
        "/api/remote-transcode/sessions/{jobId}/heartbeat",
        remote_transcode_heartbeat_endpoint,
    )
    app.router.add_get(
        "/api/remote-transcode/sessions/{jobId}/media/{filename}",
        remote_transcode_media_endpoint,
    )
