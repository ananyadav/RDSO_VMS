"""Server readiness — listen immediately, finish MongoDB/migrations in background."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from aiohttp import web

logger = logging.getLogger(__name__)

STARTUP_KEY = "startup"
STARTUP_BUDGET_SECONDS = 300  # RDSO 18.6.22.1 — initialization ≤ 5 minutes


def new_startup_state() -> Dict[str, Any]:
    return {
        "ready": False,
        "mongodb": False,
        "camera_count": 0,
        "error": None,
        "phase": "starting",
        "startup_started_at": None,
        "startup_started_mono": None,
        "listen_at": None,
        "listen_mono": None,
        "ready_at": None,
        "ready_mono": None,
        "startup_duration_seconds": None,
        "critical_services": {
            "mongodb": False,
            "indexes": False,
            "http_listen": False,
        },
    }


def get_startup(app: web.Application) -> Dict[str, Any]:
    state = app.get(STARTUP_KEY)
    if state is None:
        state = new_startup_state()
        app[STARTUP_KEY] = state
    return state


def mark_startup_begin(state: Dict[str, Any]) -> None:
    if state.get("startup_started_mono") is not None:
        return
    now = datetime.now(timezone.utc)
    state["startup_started_at"] = now.isoformat()
    state["startup_started_mono"] = time.monotonic()


def mark_listen(state: Dict[str, Any]) -> None:
    mark_startup_begin(state)
    now = datetime.now(timezone.utc)
    state["listen_at"] = now.isoformat()
    state["listen_mono"] = time.monotonic()
    crit = state.setdefault("critical_services", {})
    crit["http_listen"] = True


def mark_ready(state: Dict[str, Any]) -> None:
    mark_startup_begin(state)
    now = datetime.now(timezone.utc)
    mono = time.monotonic()
    state["ready_at"] = now.isoformat()
    state["ready_mono"] = mono
    started = state.get("startup_started_mono")
    if isinstance(started, (int, float)):
        state["startup_duration_seconds"] = round(mono - float(started), 3)
    else:
        state["startup_duration_seconds"] = None
    state["phase"] = "ready"
    state["ready"] = True


def startup_timing_public(state: Dict[str, Any]) -> dict[str, Any]:
    duration = state.get("startup_duration_seconds")
    within = None
    if duration is not None:
        within = float(duration) <= float(STARTUP_BUDGET_SECONDS)
    return {
        "startup_started_at": state.get("startup_started_at"),
        "listen_at": state.get("listen_at"),
        "ready_at": state.get("ready_at"),
        "startup_duration_seconds": duration,
        "startup_budget_seconds": STARTUP_BUDGET_SECONDS,
        "within_startup_budget": within,
        "rdso_18_6_22_1": True,
    }


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


def _vms_health_snapshot() -> dict:
    """Local VMS node identity / leadership for readiness (18.1.29)."""
    try:
        from app.services.vms_server_config import (
            local_vms_role,
            local_vms_server_id,
            vms_ha_enabled,
        )
        from app.services.vms_ha_coordinator import local_is_coordinator_sync

        return {
            "vms_server_id": local_vms_server_id(),
            "vms_role": local_vms_role(),
            "vms_ha_enabled": vms_ha_enabled(),
            "vms_local_is_leader": local_is_coordinator_sync(),
        }
    except Exception:
        return {
            "vms_server_id": None,
            "vms_role": None,
            "vms_ha_enabled": False,
            "vms_local_is_leader": True,
        }


def health_payload(state: Dict[str, Any]) -> dict[str, Any]:
    rec = _recording_health_snapshot()
    vms = _vms_health_snapshot()
    timing = startup_timing_public(state)
    crit = dict(state.get("critical_services") or {})
    crit["mongodb"] = bool(state.get("mongodb"))
    return {
        "ready": bool(state.get("ready")),
        "mongodb": bool(state.get("mongodb")),
        "cameraCount": int(state.get("camera_count") or 0),
        "phase": state.get("phase") or "starting",
        "error": state.get("error"),
        "critical_services": crit,
        "recording": rec,
        "enabled": rec["enabled"],
        "recordingActive": rec["recordingActive"],
        **vms,
        **timing,
        "rdso_18_1_29": True,
    }


async def health_handler(request: web.Request) -> web.Response:
    state = get_startup(request.app)
    return web.json_response(health_payload(state))


async def ready_handler(request: web.Request) -> web.Response:
    """Explicit readiness probe — 200 only when CCC-critical services are ready."""
    state = get_startup(request.app)
    payload = health_payload(state)
    if not payload["ready"]:
        return web.json_response(payload, status=503)
    return web.json_response(payload)


_STARTUP_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/ready",
    "/api/vms/ha/ready",
    "/api/login",
    "/api/logout",
    "/api/ccc/security",
)


def _exempt_from_startup_gate(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in _STARTUP_EXEMPT_PREFIXES):
        return True
    # SPA shell + built assets (production single-port mode)
    if (
        not path.startswith("/api/")
        and not path.startswith("/go2rtc/")
        and not path.startswith("/media/")
    ):
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
        {
            "error": "Server starting — MongoDB sync in progress, retry shortly",
            "phase": state.get("phase"),
            "ready": False,
            **startup_timing_public(state),
        },
        status=503,
    )
