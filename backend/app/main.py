import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import argparse
import asyncio
import logging
from aiohttp import web
import aiohttp_cors

from app.routes.cameras import (
    get_camera_list, get_camera_groups_endpoint, get_configured_cameras, scan_for_cameras,
    add_camera_endpoint, update_camera_endpoint, delete_camera_endpoint, import_cameras_endpoint,
    test_camera_stream_endpoint, reload_group_go2rtc_endpoint,
)
from app.routes.users import (
    get_users_list, add_user_endpoint, update_user_endpoint,
    delete_user_endpoint
)
from app.routes.auth import login_endpoint, logout_endpoint, session_endpoint

from app.routes.recording import setup_recording_routes
from app.routes.playback import setup_playback_routes
from app.routes.locations import setup_location_routes
from app.routes.go2rtc import setup_go2rtc_routes
from app.routes.ptz import setup_ptz_routes

from app.core.auth_context import session_middleware
from app.core.http_utils import json_error_middleware
from app.core.startup_state import STARTUP_KEY, health_handler, new_startup_state, startup_middleware
from app.services.video_streaming import performance_monitor, get_video_decode_mode

# --- Log configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.getLogger("aioice").setLevel(logging.WARNING)
logging.getLogger("aiortc").setLevel(logging.WARNING)


async def create_app():
    """Application factory function."""
    app = web.Application(middlewares=[json_error_middleware, startup_middleware, session_middleware])
    app[STARTUP_KEY] = new_startup_state()

    setup_recording_routes(app)
    setup_playback_routes(app)

    # Site / building / floor configuration
    setup_location_routes(app)

    setup_go2rtc_routes(app)
    setup_ptz_routes(app)

    # --- Register routes ---
    app.router.add_get("/api/cameras", get_camera_list)
    app.router.add_get("/api/cameras/groups", get_camera_groups_endpoint)
    app.router.add_get("/api/cameras/configured", get_configured_cameras)
    app.router.add_post("/api/cameras", add_camera_endpoint)
    app.router.add_post("/api/cameras/import", import_cameras_endpoint)
    app.router.add_put("/api/cameras/{id}", update_camera_endpoint)
    app.router.add_delete("/api/cameras/{id}", delete_camera_endpoint)
    app.router.add_post("/api/cameras/scan", scan_for_cameras)
    app.router.add_post("/api/cameras/{id}/test-stream", test_camera_stream_endpoint)
    app.router.add_post(
        "/api/cameras/groups/{camera_group}/reload-go2rtc",
        reload_group_go2rtc_endpoint,
    )

    app.router.add_get("/api/users", get_users_list)
    app.router.add_post("/api/users", add_user_endpoint)
    app.router.add_put("/api/users/{id}", update_user_endpoint)
    app.router.add_delete("/api/users/{id}", delete_user_endpoint)

    app.router.add_post("/api/login", login_endpoint)
    app.router.add_post("/api/logout", logout_endpoint)
    app.router.add_get("/api/auth/session", session_endpoint)
    app.router.add_get("/api/health", health_handler)

    async def status_handler(_request):
        """Return server status including real system metrics."""
        import psutil, time
        try:
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            uptime_seconds = int(time.time() - psutil.boot_time())
            hours, rem = divmod(uptime_seconds, 3600)
            minutes = rem // 60
            days = hours // 24
            hours = hours % 24
        except Exception:
            cpu = mem = disk = None
            days = hours = minutes = 0

        return web.json_response({
            "status": "ok",
            "video_decode": get_video_decode_mode(),
            "cpu_percent": cpu,
            "memory_percent": round(mem.percent, 1) if mem else None,
            "memory_used_mb": round(mem.used / 1024 / 1024) if mem else None,
            "memory_total_mb": round(mem.total / 1024 / 1024) if mem else None,
            "disk_percent": round(disk.percent, 1) if disk else None,
            "disk_used_gb": round(disk.used / 1024**3, 1) if disk else None,
            "disk_total_gb": round(disk.total / 1024**3, 1) if disk else None,
            "uptime": f"{days}d {hours}h {minutes}m",
        })

    app.router.add_get("/api/status", status_handler)

    # --- Serve static frontend (SPA) — assets at /assets/*, fallback to index.html ---
    from pathlib import Path

    def _resolve_static_dir() -> Path:
        backend_root = Path(__file__).resolve().parents[1]
        for candidate in (backend_root / 'static', backend_root.parent / 'frontend' / 'dist'):
            if (candidate / 'index.html').is_file():
                return candidate
        return backend_root / 'static'

    static_dir = _resolve_static_dir()
    _ROOT_STATIC = frozenset({'/vite.svg', '/favicon.ico'})

    async def spa_handler(request: web.Request) -> web.StreamResponse:
        path = request.path

        if path.startswith('/api') or path.startswith('/go2rtc/'):
            raise web.HTTPNotFound()

        # Mis-resolved relative assets from deep SPA routes → /assets/...
        if '/assets/' in path and not path.startswith('/assets/'):
            asset_path = path[path.index('/assets/') :]
            raise web.HTTPFound(asset_path)

        if path.startswith('/assets/') or path in _ROOT_STATIC:
            file_path = static_dir / path.lstrip('/')
            if file_path.is_file():
                return web.FileResponse(file_path)

        index_path = static_dir / 'index.html'
        if index_path.is_file():
            return web.FileResponse(index_path)

        raise web.HTTPNotFound(text="Frontend build not found. Run: cd frontend && npm run build")

    app.router.add_get('/{path:.*}', spa_handler)

    # --- CORS setup ---
    cors_defaults = {
        "http://localhost:3000": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*",
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        ),
        "http://localhost:5173": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*",
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        ),
    }
    extra_origins = os.getenv("CORS_ORIGINS", "").strip()
    for origin in (o.strip() for o in extra_origins.split(",") if o.strip()):
        cors_defaults[origin] = aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*",
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        )
    cors = aiohttp_cors.setup(app, defaults=cors_defaults)

    for route in list(app.router.routes()):
        cors.add(route)

    return app


async def run_startup_tasks(app: web.Application) -> None:
    """MongoDB ping, migrations, and go2rtc — runs after HTTP port is listening."""
    state = app[STARTUP_KEY]
    try:
        from app.core.database import (
            client,
            DATABASE_NAME,
            backfill_all_camera_rtsp_urls,
            backfill_usernames,
            camera_collection,
            ensure_database_indexes,
        )
        from app.services.camera_service import backfill_camera_locations
        from app.services.camera_identity import (
            backfill_camera_uids,
            backfill_recording_sessions_identity,
            backfill_recording_storage_ids,
        )

        state["phase"] = "mongodb"
        await client.admin.command("ping")
        state["mongodb"] = True
        print(f"[OK] MongoDB: Connected to database '{DATABASE_NAME}'")

        from app.services.location_catalog import sync_locations_catalog
        from app.services.location_store import (
            bootstrap_location_config,
            consolidate_healthcare_into_rml6,
            migrate_corporate_office_cameras,
            remediate_camera_location_fields,
            sync_all_camera_groups,
        )

        state["phase"] = "migrations"
        await ensure_database_indexes()
        skip_migrations = os.getenv("SKIP_STARTUP_MIGRATIONS", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if skip_migrations:
            print("[OK] Startup migrations skipped (SKIP_STARTUP_MIGRATIONS=1)")
        else:
            await bootstrap_location_config()
            hc_n = await consolidate_healthcare_into_rml6()
            if hc_n:
                print(f"[OK] Locations: Moved {hc_n} clinic camera(s) to RML - 6 / Healthcare Clinic")
            migrated = await migrate_corporate_office_cameras()
            if migrated:
                print(f"[OK] Locations: Migrated {migrated} Corporate Office camera(s) to RML - 6 paths")
            synced = await sync_all_camera_groups()
            if synced:
                print(f"[OK] Locations: Canonicalized camera_group for {synced} camera(s)")
            remediated = await remediate_camera_location_fields()
            if remediated:
                print(f"[OK] Locations: Remediated site/building/floor for {remediated} camera(s)")
            await sync_locations_catalog()
            print("[OK] Locations: Site/building/floor config ready")
            n = await backfill_all_camera_rtsp_urls()
            if n:
                print(f"[OK] Cameras: Backfilled RTSP URLs for {n} camera(s)")
            uid_n = await backfill_camera_uids()
            if uid_n:
                print(f"[OK] Cameras: Backfilled camera_uid for {uid_n} camera(s)")
            loc_n = await backfill_camera_locations()
            if loc_n:
                print(f"[OK] Cameras: Backfilled location fields for {loc_n} camera(s)")
            rec_n = await backfill_recording_storage_ids()
            if rec_n:
                print(f"[OK] Recordings: Mapped legacy storage folders for {rec_n} camera(s)")
            sess_n = await backfill_recording_sessions_identity()
            if sess_n:
                print(f"[OK] Recordings: Backfilled session identity for {sess_n} session(s)")
            u_n = await backfill_usernames()
            if u_n:
                print(f"[OK] Users: Backfilled username for {u_n} user(s)")

        from app.services.ffmpeg_util import ffmpeg_bin

        print(f"[OK] FFmpeg: {ffmpeg_bin()}")
        from app.services.ffmpeg_orphan_cleanup import cleanup_orphan_ffmpeg_on_startup

        killed = cleanup_orphan_ffmpeg_on_startup()
        if killed:
            print(f"[OK] Orphan FFmpeg cleanup: killed {len(killed)} process(es) {killed}")
        else:
            print("[OK] Orphan FFmpeg cleanup: none found")

        state["phase"] = "go2rtc"
        from app.services.go2rtc_service import start_go2rtc_on_startup

        await start_go2rtc_on_startup()

        state["camera_count"] = await camera_collection.count_documents({})
        state["phase"] = "ready"
        state["ready"] = True
        print(f"[OK] Startup complete — {state['camera_count']} camera(s) in database")
    except Exception as e:
        state["error"] = str(e)
        state["phase"] = "failed"
        print(f"[ERROR] Startup failed: {e}")
        logging.error("Startup failed: %s", e)


async def main():
    parser = argparse.ArgumentParser(description="NVR Backend Server")
    parser.add_argument("--api-port", type=int, default=10000, help="Port for the HTTP API server")
    parser.add_argument(
        "--api-host",
        type=str,
        default=None,
        help="Bind address (default: API_HOST env or 127.0.0.1)",
    )
    args = parser.parse_args()

    api_host = (args.api_host or os.getenv("API_HOST", "127.0.0.1")).strip() or "127.0.0.1"

    print("\n" + "=" * 60)
    print("NVR Backend Server Starting...")
    print("=" * 60)

    decode_info = get_video_decode_mode()
    print(f"[INFO] Video decode: {decode_info['description']} (set VIDEO_HWACCEL=cuda to use GPU)")

    await performance_monitor.start_monitoring()
    print("[OK] Performance monitoring: Started")

    app = await create_app()
    print("[OK] Application: Initialized")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, api_host, args.api_port)
    try:
        await site.start()
    except OSError as exc:
        winerr = getattr(exc, "winerror", None)
        if winerr == 10048 or getattr(exc, "errno", None) in (98, 10048):
            print(f"[ERROR] Port {args.api_port} is already in use.")
            print("        Run .\\start_dev.ps1 or stop the existing python process first.")
            await runner.cleanup()
            raise SystemExit(1) from exc
        raise

    print(f"[OK] Server: Listening on http://{api_host}:{args.api_port} (startup continues in background)")
    print(f"    Health: http://127.0.0.1:{args.api_port}/api/health")
    print("=" * 60 + "\n")
    logging.info("Server listening on http://%s:%s", api_host, args.api_port)

    asyncio.create_task(run_startup_tasks(app))

    async def schedule_go2rtc_after_ready() -> None:
        from app.services.go2rtc_service import GO2RTC_ENABLED, schedule_go2rtc_stream_sync

        while not app[STARTUP_KEY].get("ready"):
            if app[STARTUP_KEY].get("error"):
                return
            await asyncio.sleep(1)
        if GO2RTC_ENABLED:
            schedule_go2rtc_stream_sync(reason="startup")

    asyncio.create_task(schedule_go2rtc_after_ready())

    try:
        await asyncio.Event().wait()
    finally:
        # Cleanup all camera tracks before shutdown
        try:
            from app.services.video_streaming import cleanup_all_tracks
            await cleanup_all_tracks()
        except Exception as e:
            logging.debug(f"Error during track cleanup (expected): {e}")

        # Stop all recordings
        try:
            from app.services.video_recording import cleanup_all_recordings
            await cleanup_all_recordings()
        except Exception as e:
            logging.debug(f"Error during recording cleanup (expected): {e}")

        try:
            from app.services.ffmpeg_orphan_cleanup import shutdown_all_nvr_ffmpeg

            killed = await shutdown_all_nvr_ffmpeg()
            if killed:
                logging.info("[FFMPEG-CLEANUP] shutdown killed PIDs: %s", killed)
        except Exception as e:
            logging.debug(f"Error during FFmpeg cleanup (expected): {e}")

        try:
            from app.services.go2rtc_service import stop_go2rtc

            await stop_go2rtc()
        except Exception as e:
            logging.debug(f"Error during go2rtc cleanup (expected): {e}")

        try:
            await runner.cleanup()
        except Exception as e:
            logging.debug(f"Error during runner cleanup (expected): {e}")

        # Stop performance monitoring
        await performance_monitor.stop_monitoring()
        print("\n[STOP] Server shutting down...")
        logging.info("Performance monitoring stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user (Ctrl+C)")
        logging.info("Server shutting down.")
