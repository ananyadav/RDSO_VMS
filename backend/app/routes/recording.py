import asyncio
import io
import logging
import zipfile
from datetime import datetime, timezone
from aiohttp import web
from bson import ObjectId
from bson.errors import InvalidId

from app.core.access_control import (
    deny_unless_camera_access,
    deny_unless_playback_permission,
    deny_unless_super_admin,
    require_user,
)
from app.core.auth_context import get_effective_user
from app.core.database import (
    get_active_recording_session,
    get_recording_session,
    list_recording_sessions,
    recording_sessions_collection,
    update_recording_session,
)
from app.core.roles import is_ops_admin
from app.services.camera_access import user_can_access_camera
from app.services.camera_identity import get_camera_by_ref
from app.services.video_recording import (
    start_camera_recording,
    stop_camera_recording,
    is_camera_recording,
    get_camera_hls_info,
    finalize_orphaned_recording_sessions,
)
from app.services.recording_metrics import (
    log_active_recording_stats,
    get_disk_summary,
    backfill_all_session_stats_from_disk,
)
from app.services.storage_dashboard import get_storage_dashboard
from app.services.recording_health import get_recording_health
from app.services.storage_settings_store import (
    get_storage_settings_public,
    load_storage_settings,
    update_storage_settings,
)
from app.services.recording_retention import (
    delete_recording_session_files,
    run_retention_pass,
    get_last_retention_pass,
)
from app.services.recording_config import (
    STATUS_LOG_INTERVAL_SECONDS,
    RETENTION_PASS_INTERVAL_SECONDS,
    RecordingEngineDisabled,
    get_retention_policy,
    is_recording_engine_enabled,
)
from app.services.recording_media import (
    RecordingMediaError,
    build_recording_media_response,
    media_error_response,
    resolve_session_dir,
    validate_filename,
)
from app.services.audit_service import (
    ACTION_RECORDING_CONFIG_CHANGED,
    ACTION_RECORDING_DELETED,
    ACTION_RECORDING_EXPORT_CREATED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
    write_audit,
)
from app.services import recording_schedule_store as recording_sched

# --- Recording Global State (re-exported from recording_schedule_store) ---
monitoring_task: asyncio.Task | None = None
_metrics_tick = 0
_retention_tick = 0


def _engine_disabled_response() -> web.Response:
    return web.json_response(
        {
            "error": "Recording engine is disabled",
            "enabled": False,
            "recordingActive": False,
        },
        status=409,
    )


def _sanitized_session_path(session: dict) -> str:
    camera_id = str(session.get("camera_id") or "")
    sid = str(session.get("id") or "")
    return f"{camera_id}/sessions/{sid}"


def _camera_label(cam: dict | None, camera_id: str) -> str:
    if not cam:
        return camera_id
    return str(cam.get("name") or cam.get("ip_address") or camera_id)


async def _filter_sessions_for_user(user: dict | None, sessions: list) -> list:
    if is_ops_admin(user):
        return sessions
    out = []
    cache: dict[str, bool] = {}
    for session in sessions:
        cid = str(session.get("camera_id") or "")
        if cid not in cache:
            cam = await get_camera_by_ref(cid) if cid else None
            cache[cid] = bool(cam) and user_can_access_camera(user, cid, cam)
        if cache[cid]:
            out.append(session)
    return out


async def _audit_recording_config(request: web.Request, metadata: dict, changes: dict | None = None) -> None:
    actor = await get_effective_user(request)
    await write_audit(
        action=ACTION_RECORDING_CONFIG_CHANGED,
        actor=actor,
        resource_type="recording",
        request=request,
        success=True,
        metadata=metadata,
        changes=changes,
    )


# ----------------------------
# Background monitor task
# ----------------------------
async def monitor_recording_schedule():
    """Background task to monitor recording schedule and start/stop recordings."""
    logging.info("[RECORDING] Recording monitor task started")
    while True:
        try:
            await asyncio.sleep(5)  # Check every 5 seconds

            global _metrics_tick, _retention_tick
            _metrics_tick += 5
            _retention_tick += 5
            if _metrics_tick >= STATUS_LOG_INTERVAL_SECONDS:
                _metrics_tick = 0
                try:
                    await log_active_recording_stats()
                except Exception as e:
                    logging.error(f"[RECORDING] Metrics error: {e}", exc_info=True)
            if _retention_tick >= RETENTION_PASS_INTERVAL_SECONDS:
                _retention_tick = 0
                try:
                    await run_retention_pass()
                except Exception as e:
                    logging.error(f"[RECORDING] Retention error: {e}", exc_info=True)

            # Only record if master recording is enabled
            if not recording_sched.master_enabled:
                for camera_id in list(recording_sched.recording_schedule.keys()):
                    try:
                        if await is_camera_recording(camera_id):
                            await stop_camera_recording(camera_id)
                    except Exception as e:
                        logging.error(f"[RECORDING] Error stopping recording for {camera_id}: {e}", exc_info=True)
                continue

            # Check each camera in schedule
            for camera_id, should_record in recording_sched.recording_schedule.items():
                try:
                    is_currently_recording = await is_camera_recording(camera_id)

                    if should_record and not is_currently_recording:
                        logging.info(f"[RECORDING] Starting recording for camera {camera_id}")
                        await start_camera_recording(camera_id)

                    elif not should_record and is_currently_recording:
                        from app.services.alarm_recording_service import is_alarm_owned_recording

                        if is_alarm_owned_recording(camera_id):
                            continue
                        logging.info(f"[RECORDING] Stopping recording for camera {camera_id}")
                        await stop_camera_recording(camera_id)

                except Exception as e:
                    logging.error(f"[RECORDING] Error managing recording for camera {camera_id}: {e}", exc_info=True)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"[RECORDING] Error in recording schedule monitor: {e}", exc_info=True)
            await asyncio.sleep(5)


# ----------------------------
# Schedule endpoints
# ----------------------------
async def get_recording_schedule_endpoint(request: web.Request):
    """Get current recording schedule and status."""
    try:
        await require_user(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    # Flat boolean map — matches what App.tsx expects
    flat_schedule = {
        camera_id: bool(enabled)
        for camera_id, enabled in recording_sched.recording_schedule.items()
    }

    return web.json_response({
        "schedule": flat_schedule,
        "master_enabled": recording_sched.master_enabled
    })


async def update_recording_schedule_endpoint(request: web.Request):
    """Update recording schedule."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    data = await request.json()
    incoming = {str(k): bool(v) for k, v in (data.get("schedule") or {}).items()}
    await recording_sched.apply_schedule_update(incoming)
    await _audit_recording_config(request, {"operation": "schedule_update"})
    return web.json_response({
        "status": "ok",
        "master_enabled": recording_sched.master_enabled,
        "schedule": {
            cid: bool(enabled)
            for cid, enabled in recording_sched.recording_schedule.items()
        },
    })


async def toggle_recording_endpoint(request: web.Request):
    """Toggle recording for a specific camera. SUPER_ADMIN only (configuration)."""
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    current = recording_sched.recording_schedule.get(camera_id, False)
    next_state = not current
    recording_sched.set_camera_recording(camera_id, next_state)
    await recording_sched.save_recording_settings()
    logging.info(
        f"[RECORDING] Toggled recording for camera {camera_id}: {next_state} "
        f"(master_enabled={recording_sched.master_enabled})"
    )
    await _audit_recording_config(
        request,
        {"operation": "camera_toggle", "camera_id": camera_id, "recording": next_state},
    )
    return web.json_response({"id": camera_id, "recording": next_state})


async def recording_metrics_endpoint(request: web.Request):
    """GET /api/recordings/metrics — disk growth per hour for active recordings."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    return web.json_response(await get_disk_summary())


async def backfill_recording_stats_endpoint(request: web.Request):
    """POST /api/recordings/stats/backfill — sync all session stats from filesystem to MongoDB."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    count = await backfill_all_session_stats_from_disk()
    await _audit_recording_config(request, {"operation": "stats_backfill", "sessions_updated": count})
    return web.json_response({"status": "ok", "sessions_updated": count})


async def storage_dashboard_endpoint(request: web.Request):
    """GET /api/storage/dashboard — recordings usage, disk free space, per-camera breakdown."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    summary_only = request.rel_url.query.get("summary") == "1"
    data = await get_storage_dashboard(summary_only=summary_only)
    data["retention"] = get_retention_policy()
    data["last_retention_pass"] = get_last_retention_pass()
    return web.json_response(data)


async def retention_policy_endpoint(request: web.Request):
    """GET /api/storage/retention — configured retention window."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    return web.json_response(
        {
            "policy": get_retention_policy(),
            "last_pass": get_last_retention_pass(),
        }
    )


async def storage_settings_get_endpoint(request: web.Request):
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    return web.json_response(get_storage_settings_public())


async def storage_settings_update_endpoint(request: web.Request):
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        from app.services.storage_settings_store import get_effective_recordings_dir, get_effective_retention_days

        old_dir = (
            str(get_effective_recordings_dir())
            if body.get("recordings_dir") is not None
            else None
        )
        old_days = get_effective_retention_days()
        result = await update_storage_settings(
            retention_days=body.get("retention_days"),
            recordings_dir=body.get("recordings_dir"),
        )
        if old_dir and result.get("recordings_dir") and result["recordings_dir"] != old_dir:
            if is_recording_engine_enabled():
                await recording_sched.stop_all_scheduled_recording(persist=False)
            logging.info(
                "[STORAGE] Recordings folder changed %s -> %s; stopped active recordings",
                old_dir,
                result["recordings_dir"],
            )
        actor = await get_effective_user(request)
        ok = await commit_critical_audit(
            action=ACTION_RECORDING_CONFIG_CHANGED,
            actor=actor,
            resource_type="storage",
            request=request,
            success=True,
            metadata={"operation": "storage_settings"},
            changes={
                "retention_days": {"before": old_days, "after": result.get("retention_days")},
                "recordings_dir": {"before": old_dir, "after": result.get("recordings_dir")},
            },
        )
        if not ok:
            return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)
        return web.json_response(result)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logging.error("[STORAGE] settings update failed: %s", exc, exc_info=True)
        return web.json_response({"error": "Failed to update storage settings"}, status=500)


async def retention_run_endpoint(request: web.Request):
    """POST /api/storage/retention/run — manually trigger retention cleanup."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    if not is_recording_engine_enabled():
        return _engine_disabled_response()
    result = await run_retention_pass()
    await _audit_recording_config(request, {"operation": "retention_run", **result})
    return web.json_response({"status": "ok", **result})


async def recording_health_endpoint(request: web.Request):
    """GET /api/recordings/health — per-camera recording + FFmpeg health."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    enabled = is_recording_engine_enabled()
    if not enabled:
        return web.json_response(
            {
                "enabled": False,
                "recordingActive": False,
                "status": "disabled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "total": 0,
                    "recording": 0,
                    "healthy": 0,
                    "warning": 0,
                    "reconnecting": 0,
                    "offline": 0,
                    "idle": 0,
                },
                "cameras": [],
            }
        )
    scheduled = {cid for cid, on in recording_sched.recording_schedule.items() if on}
    payload = await get_recording_health(scheduled)
    payload["enabled"] = True
    payload["recordingActive"] = int((payload.get("summary") or {}).get("recording") or 0) > 0
    return web.json_response(payload)


async def set_master_recording_endpoint(request: web.Request):
    """Enable/disable master recording switch (stops FFmpeg when off; keeps schedule)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    data = await request.json()
    enabled = bool(data.get("enabled", False))
    if enabled and not is_recording_engine_enabled():
        return _engine_disabled_response()
    if enabled:
        if not any(recording_sched.recording_schedule.values()):
            return web.json_response(
                {"error": "No cameras scheduled — turn on at least one camera and save the schedule first"},
                status=400,
            )
        recording_sched.master_enabled = True
    else:
        recording_sched.master_enabled = False
        for camera_id in list(recording_sched.recording_schedule.keys()):
            try:
                if await is_camera_recording(camera_id):
                    await stop_camera_recording(camera_id)
            except Exception as exc:
                logging.error("[RECORDING] Error stopping %s on master off: %s", camera_id, exc)

    await recording_sched.save_recording_settings()
    await _audit_recording_config(
        request,
        {"operation": "master_recording", "master_enabled": recording_sched.master_enabled},
    )
    return web.json_response({"status": "ok", "master_enabled": recording_sched.master_enabled})


async def stop_all_recording_endpoint(request: web.Request):
    """POST /api/recordings/stop-all — stop every camera immediately."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    result = await recording_sched.stop_all_scheduled_recording(persist=True)
    await _audit_recording_config(request, {"operation": "stop_all", **result})
    return web.json_response({"status": "ok", **result})


# ----------------------------
# Explicit start / stop + session metadata
# ----------------------------
async def start_recording_endpoint(request: web.Request):
    """POST /api/recordings/{cameraId}/start — begin RTSP recording session."""
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    if not is_recording_engine_enabled():
        return _engine_disabled_response()
    try:
        session = await start_camera_recording(camera_id)
        recording_sched.set_camera_recording(camera_id, True)
        await recording_sched.save_recording_settings()
        await _audit_recording_config(request, {"operation": "start", "camera_id": camera_id})
        return web.json_response(
            {"status": "recording", "camera_id": camera_id, "session": session},
            status=200,
        )
    except RecordingEngineDisabled:
        return _engine_disabled_response()
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    except Exception as e:
        logging.error(f"[RECORDING] Start failed for {camera_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def stop_recording_endpoint(request: web.Request):
    """POST /api/recordings/{cameraId}/stop — end session and save metadata."""
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        session = await stop_camera_recording(camera_id)
        recording_sched.set_camera_recording(camera_id, False)
        await recording_sched.save_recording_settings()
        await _audit_recording_config(request, {"operation": "stop", "camera_id": camera_id})
        return web.json_response(
            {"status": "stopped", "camera_id": camera_id, "session": session},
            status=200,
        )
    except Exception as e:
        logging.error(f"[RECORDING] Stop failed for {camera_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def list_camera_sessions_endpoint(request: web.Request):
    """GET /api/recordings/{cameraId}/sessions"""
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id or "")
    if denied is not None:
        return denied
    limit = int(request.rel_url.query.get("limit", "50"))
    sessions = await list_recording_sessions(camera_id=camera_id, limit=limit)
    return web.json_response({"camera_id": camera_id, "sessions": sessions})


async def list_all_sessions_endpoint(request: web.Request):
    """GET /api/recordings/sessions"""
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    limit = int(request.rel_url.query.get("limit", "50"))
    sessions = await list_recording_sessions(camera_id=None, limit=limit)
    sessions = await _filter_sessions_for_user(user, sessions)
    return web.json_response({"sessions": sessions})


async def get_session_endpoint(request: web.Request):
    """GET /api/recordings/sessions/{sessionId}"""
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    session_id = request.match_info.get("sessionId")
    session = await get_recording_session(session_id)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    denied = await deny_unless_camera_access(request, session.get("camera_id") or "")
    if denied is not None:
        return denied
    return web.json_response(session)


async def delete_session_endpoint(request: web.Request):
    """DELETE /api/recordings/sessions/{sessionId} — SUPER_ADMIN only."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    session_id = request.match_info.get("sessionId")
    try:
        oid = ObjectId(session_id)
    except (InvalidId, TypeError):
        return web.json_response({"error": "Session not found"}, status=404)
    raw = await recording_sessions_collection.find_one({"_id": oid})
    session = await get_recording_session(session_id)
    if not raw or not session:
        return web.json_response({"error": "Session not found"}, status=404)
    if session.get("status") == "recording":
        return web.json_response(
            {"error": "Stop the recording session before deleting it"},
            status=409,
        )

    camera_id = str(session.get("camera_id") or "")
    cam = await get_camera_by_ref(camera_id) if camera_id else None
    now = datetime.now(timezone.utc).isoformat()
    await update_recording_session(
        session_id,
        {"status": "deleted", "deleted_at": now, "stopped_at": session.get("stopped_at") or now},
    )

    actor = await get_effective_user(request)

    async def _compensate():
        await recording_sessions_collection.replace_one({"_id": raw["_id"]}, raw, upsert=True)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_RECORDING_DELETED,
        actor=actor,
        resource_type="recording",
        resource_id=session_id,
        resource_label=_camera_label(cam, camera_id),
        request=request,
        success=True,
        metadata={
            "camera_id": camera_id,
            "camera_label": _camera_label(cam, camera_id),
            "session_id": session_id,
            "started_at": session.get("started_at"),
            "stopped_at": session.get("stopped_at"),
            "path": _sanitized_session_path(session),
            "result": "deleted",
        },
    )
    if not ok:
        return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)

    await delete_recording_session_files(camera_id, session_id)
    return web.json_response({"status": "deleted", "id": session_id})


async def _session_download_response(request: web.Request) -> web.Response:
    """SUPER_ADMIN download/export of an existing session (zip of playlist + segments)."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    session_id = request.match_info.get("sessionId")
    session = await get_recording_session(session_id)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    camera_id = str(session.get("camera_id") or "")
    try:
        session_dir = await resolve_session_dir(camera_id, session_id)
    except RecordingMediaError as e:
        return web.json_response({"error": e.message}, status=e.status)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(session_dir.iterdir()):
            if not path.is_file():
                continue
            try:
                validate_filename(path.name)
            except RecordingMediaError:
                continue
            zf.write(path, path.name)
    payload = buf.getvalue()
    actor = await get_effective_user(request)
    await write_audit(
        action=ACTION_RECORDING_EXPORT_CREATED,
        actor=actor,
        resource_type="recording",
        resource_id=session_id,
        resource_label=_sanitized_session_path(session),
        request=request,
        success=True,
        metadata={"camera_id": camera_id, "path": _sanitized_session_path(session), "bytes": len(payload)},
    )
    filename = f"recording-{session_id}.zip"
    return web.Response(
        body=payload,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


async def download_session_endpoint(request: web.Request):
    """GET /api/recordings/sessions/{sessionId}/download"""
    return await _session_download_response(request)


async def export_session_endpoint(request: web.Request):
    """GET /api/recordings/sessions/{sessionId}/export"""
    return await _session_download_response(request)


# ----------------------------
# HLS status + serving endpoints
# ----------------------------
async def list_recording_segments_endpoint(request: web.Request):
    """
    List recorded .ts segments for a camera session.
    Query: sessionId (required) — session under Recordings/{cameraId}/sessions/{sessionId}/
    """
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id or "")
    if denied is not None:
        return denied
    session_id = request.rel_url.query.get("sessionId", "").strip()

    if not session_id:
        return web.json_response({"error": "sessionId is required"}, status=400)

    try:
        session_dir = await resolve_session_dir(camera_id, session_id)
    except RecordingMediaError as e:
        return web.json_response({"error": e.message}, status=e.status)

    segments = []
    for pattern in ("seg_*.ts", "*.ts"):
        for f in sorted(session_dir.glob(pattern)):
            if f.suffix.lower() != ".ts":
                continue
            stat = f.stat()
            segments.append({
                "filename": f.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        if segments:
            break

    return web.json_response({
        "segments": segments,
        "camera_id": camera_id,
        "session_id": session_id,
    })


async def play_recording_file_endpoint(request: web.Request):
    """
    Serve a recorded segment file (legacy path).
    Query: sessionId (required).
    Prefer GET /api/playback/{cameraId}/{sessionId}/media/{filename}
    """
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id or "")
    if denied is not None:
        return denied
    filename = request.match_info.get("filename")
    session_id = request.rel_url.query.get("sessionId", "").strip()

    if not session_id:
        return web.json_response({"error": "sessionId is required"}, status=400)

    try:
        return await build_recording_media_response(camera_id, session_id, filename)
    except RecordingMediaError as e:
        return media_error_response(e)


async def get_recording_status_endpoint(request: web.Request):
    """Returns HLS playlist info, active session, and recording status."""
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    info = await get_camera_hls_info(camera_id)
    info["is_recording"] = await is_camera_recording(camera_id)
    active = await get_active_recording_session(camera_id)
    if active:
        info["active_session"] = active
    return web.json_response(info)


async def serve_hls_file_endpoint(request: web.Request):
    """
    Serve HLS playlist/segments (legacy path).
    Query: sessionId (required).
    Prefer GET /api/playback/{cameraId}/{sessionId}/media/{filename}
    """
    camera_id = request.match_info.get("cameraId")
    denied = await deny_unless_playback_permission(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id or "")
    if denied is not None:
        return denied
    filename = request.match_info.get("filename")
    session_id = request.rel_url.query.get("sessionId", "").strip()

    if not session_id:
        return web.json_response({"error": "sessionId is required"}, status=400)

    try:
        return await build_recording_media_response(camera_id, session_id, filename)
    except RecordingMediaError as e:
        return media_error_response(e)


async def maybe_start_recording_engine() -> bool:
    """Start recording-engine background jobs only when RECORDING_ENABLED=true.

    Returns True when the monitor loop was scheduled. Playback APIs stay available either way.
    """
    global monitoring_task
    if not is_recording_engine_enabled():
        logging.info(
            "[RECORDING] Engine disabled (RECORDING_ENABLED=false); "
            "skipping monitor, retention, metrics, and recorder startup. Playback remains available."
        )
        return False
    await finalize_orphaned_recording_sessions(stop_reason="backend_restart")
    backfilled = await backfill_all_session_stats_from_disk()
    if backfilled:
        logging.info(f"[RECORDING] Backfilled filesystem stats for {backfilled} session(s)")
    try:
        retention_result = await run_retention_pass()
        logging.info(
            f"[RECORDING] Startup retention: freed {retention_result.get('freed_gb', 0)} GB"
        )
    except Exception as e:
        logging.error(f"[RECORDING] Startup retention failed: {e}", exc_info=True)
    if monitoring_task is None or monitoring_task.done():
        monitoring_task = asyncio.create_task(monitor_recording_schedule())
        logging.info("[RECORDING] Monitor task scheduled")
    return True


# ----------------------------
# Optional: helper to attach routes + start monitor
# ----------------------------
def setup_recording_routes(app: web.Application):
    """
    Call this once from your main app init file.
    Registers all endpoints and starts the monitor task on startup.
    """

    # Endpoints
    app.router.add_get("/api/recordings/schedule", get_recording_schedule_endpoint)
    app.router.add_post("/api/recordings/schedule", update_recording_schedule_endpoint)

    app.router.add_post("/api/recordings/{cameraId}/toggle", toggle_recording_endpoint)
    app.router.add_post("/api/recordings/{cameraId}/start", start_recording_endpoint)
    app.router.add_post("/api/recordings/{cameraId}/stop", stop_recording_endpoint)
    app.router.add_post("/api/recordings/master", set_master_recording_endpoint)
    app.router.add_post("/api/recordings/stop-all", stop_all_recording_endpoint)
    app.router.add_get("/api/recordings/metrics", recording_metrics_endpoint)
    app.router.add_post("/api/recordings/stats/backfill", backfill_recording_stats_endpoint)
    app.router.add_get("/api/storage/dashboard", storage_dashboard_endpoint)
    app.router.add_get("/api/storage/settings", storage_settings_get_endpoint)
    app.router.add_put("/api/storage/settings", storage_settings_update_endpoint)
    app.router.add_get("/api/storage/retention", retention_policy_endpoint)
    app.router.add_post("/api/storage/retention/run", retention_run_endpoint)
    app.router.add_get("/api/recordings/health", recording_health_endpoint)

    app.router.add_get("/api/recordings/sessions", list_all_sessions_endpoint)
    app.router.add_get("/api/recordings/sessions/{sessionId}/download", download_session_endpoint)
    app.router.add_get("/api/recordings/sessions/{sessionId}/export", export_session_endpoint)
    app.router.add_delete("/api/recordings/sessions/{sessionId}", delete_session_endpoint)
    app.router.add_get("/api/recordings/sessions/{sessionId}", get_session_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/sessions", list_camera_sessions_endpoint)

    app.router.add_get("/api/recordings/{cameraId}/status", get_recording_status_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/hls/{filename}", serve_hls_file_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/segments", list_recording_segments_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/play/{filename}", play_recording_file_endpoint)

    # Startup / cleanup for monitor task
    async def on_startup(app: web.Application):
        await load_storage_settings()
        await recording_sched.bootstrap_recording_schedule()
        from app.core.database import cleanup_legacy_pilot_recording

        await cleanup_legacy_pilot_recording()
        await maybe_start_recording_engine()

    async def on_cleanup(app: web.Application):
        global monitoring_task
        if monitoring_task:
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass
            monitoring_task = None
            logging.info("[RECORDING] Monitor task stopped")

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
