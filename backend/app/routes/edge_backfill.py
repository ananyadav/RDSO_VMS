"""API routes for RDSO 18.3.16 edge storage failover & backfill."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

from app.core.access_control import deny_unless_super_admin
from app.core.auth_context import get_effective_user
from app.services.audit_service import ACTION_EDGE_BACKFILL_STARTED, write_audit
from app.services.edge_storage_types import parse_iso

logger = logging.getLogger(__name__)


def _parse_range(request: web.Request, body: dict | None = None) -> tuple[datetime, datetime]:
    body = body or {}
    q = request.rel_url.query
    start_raw = body.get("from") or body.get("start") or q.get("from") or q.get("start")
    end_raw = body.get("to") or body.get("end") or q.get("to") or q.get("end")
    start = parse_iso(str(start_raw) if start_raw else None)
    end = parse_iso(str(end_raw) if end_raw else None)
    if start is None or end is None:
        # Default: last 6 hours
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=6)
    if end <= start:
        raise ValueError("end must be after start")
    return start, end


async def edge_capability_endpoint(request: web.Request):
    """GET /api/recordings/edge/capability/{cameraId}"""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    camera_id = request.match_info.get("cameraId") or ""
    from app.services.edge_backfill_service import get_edge_capability_for_camera

    try:
        cap = await get_edge_capability_for_camera(camera_id)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    return web.json_response(cap)


async def edge_gaps_endpoint(request: web.Request):
    """GET /api/recordings/edge/gaps?cameraId=&from=&to= — VMS gaps + optional edge overlap preview."""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    camera_id = request.rel_url.query.get("cameraId") or request.rel_url.query.get("camera_id")
    if not camera_id:
        return web.json_response({"error": "cameraId required"}, status=400)
    try:
        start, end = _parse_range(request)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    from app.services.edge_backfill_service import preview_edge_backfill

    try:
        data = await preview_edge_backfill(camera_id, start, end)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    return web.json_response(data)


async def edge_backfill_start_endpoint(request: web.Request):
    """POST /api/recordings/edge/backfill
    Body: {camera_id, from, to, confirm: true, force?: false}
    """
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    camera_id = str(body.get("camera_id") or body.get("cameraId") or "").strip()
    if not camera_id:
        return web.json_response({"error": "camera_id required"}, status=400)
    try:
        start, end = _parse_range(request, body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    confirm = body.get("confirm") is True
    force = body.get("force") is True
    from app.services.edge_backfill_service import start_edge_backfill

    try:
        result = await start_edge_backfill(
            camera_id, start, end, confirm=confirm, force=force
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    status = 200 if result.get("ok") else 409
    if result.get("ok") or result.get("job_id"):
        actor = await get_effective_user(request)
        await write_audit(
            action=ACTION_EDGE_BACKFILL_STARTED,
            actor=actor,
            resource_type="camera",
            resource_id=camera_id,
            request=request,
            success=bool(result.get("ok")),
            metadata={
                "camera_id": camera_id,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "job_id": result.get("job_id"),
                "force": force,
                "confirm": confirm,
            },
        )
    return web.json_response(result, status=status)


async def edge_jobs_list_endpoint(request: web.Request):
    """GET /api/recordings/edge/jobs?cameraId=&limit="""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    camera_id = request.rel_url.query.get("cameraId") or request.rel_url.query.get("camera_id")
    try:
        limit = int(request.rel_url.query.get("limit") or 50)
    except ValueError:
        limit = 50
    from app.services.edge_backfill_service import list_edge_jobs

    jobs = await list_edge_jobs(camera_id=camera_id, limit=limit)
    return web.json_response({"jobs": jobs, "count": len(jobs)})


async def edge_job_get_endpoint(request: web.Request):
    """GET /api/recordings/edge/jobs/{jobId}"""
    denied = await deny_unless_super_admin(request)
    if denied is not None:
        return denied
    job_id = request.match_info.get("jobId") or ""
    from app.services.edge_backfill_service import get_edge_job

    job = await get_edge_job(job_id)
    if not job:
        return web.json_response({"error": "job not found"}, status=404)
    return web.json_response(job)


def setup_edge_backfill_routes(app: web.Application) -> None:
    app.router.add_get(
        "/api/recordings/edge/capability/{cameraId}", edge_capability_endpoint
    )
    app.router.add_get("/api/recordings/edge/gaps", edge_gaps_endpoint)
    app.router.add_post("/api/recordings/edge/backfill", edge_backfill_start_endpoint)
    app.router.add_get("/api/recordings/edge/jobs", edge_jobs_list_endpoint)
    app.router.add_get("/api/recordings/edge/jobs/{jobId}", edge_job_get_endpoint)
