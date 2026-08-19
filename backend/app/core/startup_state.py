"""Server readiness — listen immediately, finish MongoDB/migrations in background."""

from __future__ import annotations

import logging
from typing import Any, Dict

from aiohttp import web

logger = logging.getLogger(__name__)

STARTUP_KEY = "startup"


def new_startup_state() -> Dict[str, Any]:
    return {
        "ready": False,
        "mongodb": False,
        "camera_count": 0,
        "error": None,
        "phase": "starting",
    }


def get_startup(app: web.Application) -> Dict[str, Any]:
    state = app.get(STARTUP_KEY)
    if state is None:
        state = new_startup_state()
        app[STARTUP_KEY] = state
    return state


def _recording_health_snapshot() -> dict:
    """Truthful disabled/active recording state for System Health. Does not start recorders."""
    from app.services.recording_config import is_recording_engine_enabled

    enabled = is_recording_engine_enabled()
    recording_active = False
    if enabled:
        try:
            from app.services.video_recording import ACTIVE_RECORDINGS

            recording_active = bool(ACTIVE_RECORDINGS)
        except Exception:
            recording_active = False
    return {"enabled": enabled, "recordingActive": recording_active}


async def health_handler(request: web.Request) -> web.Response:
    state = get_startup(request.app)
    rec = _recording_health_snapshot()
    return web.json_response(
        {
            "ready": bool(state.get("ready")),
            "mongodb": bool(state.get("mongodb")),
            "cameraCount": int(state.get("camera_count") or 0),
            "phase": state.get("phase") or "starting",
            "error": state.get("error"),
            "recording": rec,
            "enabled": rec["enabled"],
            "recordingActive": rec["recordingActive"],
        }
    )


_STARTUP_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/login",
    "/api/logout",
)


def _exempt_from_startup_gate(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in _STARTUP_EXEMPT_PREFIXES):
        return True
    # SPA shell + built assets (production single-port mode)
    if not path.startswith("/api/") and not path.startswith("/go2rtc/") and not path.startswith("/media/"):
        return True
    return False


@web.middleware
async def startup_middleware(request: web.Request, handler):
    """Return 503 for API routes until background startup completes."""
    path = request.path or ""
    if _exempt_from_startup_gate(path):
        return await handler(request)

    state = get_startup(request.app)
    if state.get("ready"):
        return await handler(request)

    err = state.get("error")
    if err:
        return web.json_response(
            {"error": f"Server startup failed: {err}"},
            status=503,
        )
    return web.json_response(
        {"error": "Server starting — MongoDB sync in progress, retry shortly"},
        status=503,
    )
