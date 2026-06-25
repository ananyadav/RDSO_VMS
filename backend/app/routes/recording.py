import asyncio
import logging
from datetime import datetime
from aiohttp import web

from app.core.database import (
    get_active_recording_session,
    get_recording_session,
    list_recording_sessions,
)
from app.services.video_recording import (
    start_camera_recording,
    stop_camera_recording,
    is_camera_recording,
    get_camera_hls_info,
    finalize_orphaned_recording_sessions,
)
from app.services.recording_pilot import (
    start_pilot,
    stop_pilot,
    check_pilot_expiry,
    resume_pilot_on_startup,
    pilot_status,
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
from app.services.recording_retention import run_retention_pass, get_last_retention_pass
from app.services.recording_config import (
    STATUS_LOG_INTERVAL_SECONDS,
    RETENTION_PASS_INTERVAL_SECONDS,
    get_retention_policy,
)
from app.services.recording_media import (
    RecordingMediaError,
    build_recording_media_response,
    media_error_response,
    resolve_session_dir,
)
from app.services import recording_schedule_store as recording_sched

# --- Recording Global State (re-exported from recording_schedule_store) ---
monitoring_task: asyncio.Task | None = None
_metrics_tick = 0
_retention_tick = 0


# ----------------------------
# Background monitor task
# ----------------------------
async def monitor_recording_schedule():
    """Background task to monitor recording schedule and start/stop recordings."""
    logging.info("[RECORDING] Recording monitor task started")
    while True:
        try:
            await asyncio.sleep(5)  # Check every 5 seconds

            await check_pilot_expiry()

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
    """Get current recording schedule and status.
    Returns a flat {camera_id: bool} schedule so the frontend can consume it directly.
    """
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
    data = await request.json()
    incoming = {str(k): bool(v) for k, v in (data.get("schedule") or {}).items()}
    await recording_sched.apply_schedule_update(incoming)
    return web.json_response({
        "status": "ok",
        "master_enabled": recording_sched.master_enabled,
        "schedule": {
            cid: bool(enabled)
            for cid, enabled in recording_sched.recording_schedule.items()
        },
    })


async def toggle_recording_endpoint(request: web.Request):
    """Toggle recording for a specific camera."""
    camera_id = request.match_info.get("cameraId")
    current = recording_sched.recording_schedule.get(camera_id, False)
    next_state = not current
    recording_sched.set_camera_recording(camera_id, next_state)
    await recording_sched.save_recording_settings()
    logging.info(
        f"[RECORDING] Toggled recording for camera {camera_id}: {next_state} "
        f"(master_enabled={recording_sched.master_enabled})"
    )
    return web.json_response({"id": camera_id, "recording": next_state})


async def pilot_start_endpoint(request: web.Request):
    """POST /api/recordings/pilot/start — Phase 1: record 2 cameras (default 4 days)."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    camera_ids = data.get("cameraIds") or data.get("camera_ids")
    hours = float(data.get("hours", 24))
    try:
        recording_sched.master_enabled = True
        result = await start_pilot(camera_ids=camera_ids, hours=hours)
        for cid in result.get("camera_ids", []):
            recording_sched.recording_schedule[cid] = True
        await recording_sched.save_recording_settings()
        return web.json_response({"status": "ok", **result})
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logging.error(f"[PILOT] Start failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def pilot_stop_endpoint(request: web.Request):
    """POST /api/recordings/pilot/stop"""
    result = await stop_pilot(reason="api")
    if result:
        for cid in result.get("camera_ids", []):
            recording_sched.recording_schedule[cid] = False
        await recording_sched.save_recording_settings()
    return web.json_response({"status": "ok", "pilot": result})


async def pilot_status_endpoint(_request: web.Request):
    """GET /api/recordings/pilot/status"""
    status = await pilot_status()
    status["disk"] = await get_disk_summary()
    return web.json_response(status)


async def recording_metrics_endpoint(_request: web.Request):
    """GET /api/recordings/metrics — disk growth per hour for active recordings."""
    return web.json_response(await get_disk_summary())


async def backfill_recording_stats_endpoint(_request: web.Request):
    """POST /api/recordings/stats/backfill — sync all session stats from filesystem to MongoDB."""
    count = await backfill_all_session_stats_from_disk()
    return web.json_response({"status": "ok", "sessions_updated": count})


async def storage_dashboard_endpoint(request: web.Request):
    """GET /api/storage/dashboard — recordings usage, disk free space, per-camera breakdown."""
    summary_only = request.rel_url.query.get("summary") == "1"
    data = await get_storage_dashboard(summary_only=summary_only)
    data["retention"] = get_retention_policy()
    data["last_retention_pass"] = get_last_retention_pass()
    return web.json_response(data)


async def retention_policy_endpoint(_request: web.Request):
    """GET /api/storage/retention — configured retention window."""
    return web.json_response(
        {
            "policy": get_retention_policy(),
            "last_pass": get_last_retention_pass(),
        }
    )


async def storage_settings_get_endpoint(_request: web.Request):
    return web.json_response(get_storage_settings_public())


async def storage_settings_update_endpoint(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        from app.services.storage_settings_store import get_effective_recordings_dir

        old_dir = (
            str(get_effective_recordings_dir())
            if body.get("recordings_dir") is not None
            else None
        )
        result = await update_storage_settings(
            retention_days=body.get("retention_days"),
            recordings_dir=body.get("recordings_dir"),
        )
        if old_dir and result.get("recordings_dir") and result["recordings_dir"] != old_dir:
            await recording_sched.stop_all_scheduled_recording(persist=False)
            logging.info(
                "[STORAGE] Recordings folder changed %s -> %s; stopped active recordings",
                old_dir,
                result["recordings_dir"],
            )
        return web.json_response(result)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logging.error("[STORAGE] settings update failed: %s", exc, exc_info=True)
        return web.json_response({"error": "Failed to update storage settings"}, status=500)


async def retention_run_endpoint(_request: web.Request):
    """POST /api/storage/retention/run — manually trigger retention cleanup."""
    result = await run_retention_pass()
    return web.json_response({"status": "ok", **result})


async def recording_health_endpoint(_request: web.Request):
    """GET /api/recordings/health — per-camera recording + FFmpeg health."""
    scheduled = {cid for cid, on in recording_sched.recording_schedule.items() if on}
    return web.json_response(await get_recording_health(scheduled))


async def set_master_recording_endpoint(request: web.Request):
    """Enable/disable master recording switch (stops FFmpeg when off; keeps schedule)."""
    data = await request.json()
    enabled = bool(data.get("enabled", False))
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
    return web.json_response({"status": "ok", "master_enabled": recording_sched.master_enabled})


async def stop_all_recording_endpoint(_request: web.Request):
    """POST /api/recordings/stop-all — stop every camera immediately."""
    result = await recording_sched.stop_all_scheduled_recording(persist=True)
    return web.json_response({"status": "ok", **result})


# ----------------------------
# Explicit start / stop + session metadata
# ----------------------------
async def start_recording_endpoint(request: web.Request):
    """POST /api/recordings/{cameraId}/start — begin RTSP recording session."""
    camera_id = request.match_info.get("cameraId")
    try:
        session = await start_camera_recording(camera_id)
        recording_sched.set_camera_recording(camera_id, True)
        await recording_sched.save_recording_settings()
        return web.json_response(
            {"status": "recording", "camera_id": camera_id, "session": session},
            status=200,
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)
    except Exception as e:
        logging.error(f"[RECORDING] Start failed for {camera_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def stop_recording_endpoint(request: web.Request):
    """POST /api/recordings/{cameraId}/stop — end session and save metadata."""
    camera_id = request.match_info.get("cameraId")
    try:
        session = await stop_camera_recording(camera_id)
        recording_sched.set_camera_recording(camera_id, False)
        await recording_sched.save_recording_settings()
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
    limit = int(request.rel_url.query.get("limit", "50"))
    sessions = await list_recording_sessions(camera_id=camera_id, limit=limit)
    return web.json_response({"camera_id": camera_id, "sessions": sessions})


async def list_all_sessions_endpoint(request: web.Request):
    """GET /api/recordings/sessions"""
    limit = int(request.rel_url.query.get("limit", "50"))
    sessions = await list_recording_sessions(camera_id=None, limit=limit)
    return web.json_response({"sessions": sessions})


async def get_session_endpoint(request: web.Request):
    """GET /api/recordings/sessions/{sessionId}"""
    session_id = request.match_info.get("sessionId")
    session = await get_recording_session(session_id)
    if not session:
        return web.json_response({"error": "Session not found"}, status=404)
    return web.json_response(session)


# ----------------------------
# HLS status + serving endpoints
# ----------------------------
async def list_recording_segments_endpoint(request: web.Request):
    """
    List recorded .ts segments for a camera session.
    Query: sessionId (required) — session under Recordings/{cameraId}/sessions/{sessionId}/
    """
    camera_id = request.match_info.get("cameraId")
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
    filename = request.match_info.get("filename")
    session_id = request.rel_url.query.get("sessionId", "").strip()

    if not session_id:
        return web.json_response({"error": "sessionId is required"}, status=400)

    try:
        return await build_recording_media_response(camera_id, session_id, filename)
    except RecordingMediaError as e:
        return media_error_response(e)


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
    app.router.add_post("/api/recordings/pilot/start", pilot_start_endpoint)
    app.router.add_post("/api/recordings/pilot/stop", pilot_stop_endpoint)
    app.router.add_get("/api/recordings/pilot/status", pilot_status_endpoint)
    app.router.add_get("/api/recordings/metrics", recording_metrics_endpoint)
    app.router.add_post("/api/recordings/stats/backfill", backfill_recording_stats_endpoint)
    app.router.add_get("/api/storage/dashboard", storage_dashboard_endpoint)
    app.router.add_get("/api/storage/settings", storage_settings_get_endpoint)
    app.router.add_put("/api/storage/settings", storage_settings_update_endpoint)
    app.router.add_get("/api/storage/retention", retention_policy_endpoint)
    app.router.add_post("/api/storage/retention/run", retention_run_endpoint)
    app.router.add_get("/api/recordings/health", recording_health_endpoint)

    app.router.add_get("/api/recordings/sessions", list_all_sessions_endpoint)
    app.router.add_get("/api/recordings/sessions/{sessionId}", get_session_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/sessions", list_camera_sessions_endpoint)

    app.router.add_get("/api/recordings/{cameraId}/status", get_recording_status_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/hls/{filename}", serve_hls_file_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/segments", list_recording_segments_endpoint)
    app.router.add_get("/api/recordings/{cameraId}/play/{filename}", play_recording_file_endpoint)

    # Startup / cleanup for monitor task
    async def on_startup(app: web.Application):
        global monitoring_task
        await load_storage_settings()
        await recording_sched.bootstrap_recording_schedule()
        # Sync disk stats and close orphaned sessions (no FFmpeg after crash/restart)
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
        asyncio.create_task(resume_pilot_on_startup(recording_sched.recording_schedule))
        if monitoring_task is None or monitoring_task.done():
            monitoring_task = asyncio.create_task(monitor_recording_schedule())
            logging.info("[RECORDING] Monitor task scheduled")

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
