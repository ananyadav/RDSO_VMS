"""ONVIF / interoperability API routes (RDSO 18.3.15 / 18.1.30)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web
from bson import ObjectId

from app.core.access_control import deny_unless_admin, deny_unless_camera_access
from app.core.database import camera_collection
from app.services.onvif_interop import get_camera_interop_summary, get_system_interop_status
from app.services.onvif_stream_uri import resolve_onvif_profile_s_streams
from app.services.rtsp_utils import mask_rtsp_url

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
_INTEGRATION_MD = _DOCS_DIR / "integration-api.md"
_OPENAPI_JSON = _DOCS_DIR / "integration-openapi.json"


async def _load_camera(camera_id: str) -> dict | None:
    if not camera_id or not ObjectId.is_valid(camera_id):
        return None
    return await camera_collection.find_one({"_id": ObjectId(camera_id)})


def _public_resolve_payload(resolved: dict) -> dict:
    out = dict(resolved)
    for role in ("main", "sub"):
        block = out.get(role)
        if not isinstance(block, dict):
            continue
        block = dict(block)
        if block.get("rtsp_uri"):
            block["rtsp_uri_masked"] = mask_rtsp_url(block["rtsp_uri"])
            block.pop("rtsp_uri", None)
        out[role] = block
    return out


async def onvif_profile_s_endpoint(request: web.Request):
    """GET /api/cameras/{id}/onvif/profile-s — profiles + GetStreamUri (masked)."""
    camera_id = request.match_info.get("id") or ""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    cam = await _load_camera(camera_id)
    if not cam:
        return web.json_response({"error": "Camera not found"}, status=404)
    resolved = await resolve_onvif_profile_s_streams(cam)
    return web.json_response(_public_resolve_payload(resolved))


async def onvif_resolve_streams_endpoint(request: web.Request):
    """POST /api/cameras/{id}/onvif/resolve-streams
    Body: { persist?: bool } — resolve GetStreamUri; optionally save to camera.
    """
    camera_id = request.match_info.get("id") or ""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    cam = await _load_camera(camera_id)
    if not cam:
        return web.json_response({"error": "Camera not found"}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    persist = body.get("persist") is True

    from app.services.onvif_stream_uri import ensure_onvif_recording_urls

    # Force Profile S resolve even for brand cameras when explicitly requested
    cam_force = dict(cam)
    cam_force["rtsp_url_source"] = "onvif_getstreamuri"
    ensured = await ensure_onvif_recording_urls(cam_force)
    resolved = ensured.get("resolve") or await resolve_onvif_profile_s_streams(cam)
    persisted = False
    if persist and ensured.get("applied"):
        updated = ensured["camera"]
        await camera_collection.update_one(
            {"_id": cam["_id"]},
            {
                "$set": {
                    "main_rtsp_url": updated.get("main_rtsp_url"),
                    "sub_rtsp_url": updated.get("sub_rtsp_url"),
                    "rtsp_url_source": updated.get("rtsp_url_source"),
                    "onvif_profile_s": updated.get("onvif_profile_s"),
                }
            },
        )
        persisted = True
    status = 200 if ensured.get("ok") or resolved.get("supported") else 422
    return web.json_response(
        {
            "ok": bool(ensured.get("ok") or resolved.get("supported")),
            "persisted": persisted,
            "message": ensured.get("message") or resolved.get("message"),
            "resolve": _public_resolve_payload(resolved),
        },
        status=status,
    )


async def camera_interop_endpoint(request: web.Request):
    """GET /api/cameras/{id}/interop — Profile S/G + vendor capability summary."""
    camera_id = request.match_info.get("id") or ""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    denied = await deny_unless_camera_access(request, camera_id)
    if denied is not None:
        return denied
    cam = await _load_camera(camera_id)
    if not cam:
        return web.json_response({"error": "Camera not found"}, status=404)
    resolve = (request.rel_url.query.get("resolve") or "").lower() in ("1", "true", "yes")
    summary = await get_camera_interop_summary(cam, resolve_streams=resolve)
    return web.json_response(summary)


async def system_interop_endpoint(request: web.Request):
    """GET /api/system/interop — RDSO 18.1.30 fleet/software interop status."""
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    limit_raw = request.rel_url.query.get("limit")
    limit = 200
    if limit_raw not in (None, ""):
        try:
            limit = int(limit_raw)
        except ValueError:
            return web.json_response({"error": "Invalid limit"}, status=400)
    status = await get_system_interop_status(camera_limit=limit)
    return web.json_response(status)


async def integration_docs_endpoint(_request: web.Request):
    """GET /api/docs/integration — markdown integration guide."""
    if _INTEGRATION_MD.is_file():
        text = _INTEGRATION_MD.read_text(encoding="utf-8")
        return web.Response(text=text, content_type="text/markdown; charset=utf-8")
    return web.json_response(
        {
            "error": "Integration docs missing",
            "hint": "See docs/integration-api.md in the repository",
        },
        status=404,
    )


async def integration_openapi_endpoint(_request: web.Request):
    """GET /api/docs/integration/openapi.json"""
    if _OPENAPI_JSON.is_file():
        data = json.loads(_OPENAPI_JSON.read_text(encoding="utf-8"))
        return web.json_response(data)
    return web.json_response({"error": "OpenAPI schema missing"}, status=404)


def setup_onvif_interop_routes(app: web.Application) -> None:
    app.router.add_get("/api/cameras/{id}/onvif/profile-s", onvif_profile_s_endpoint)
    app.router.add_post(
        "/api/cameras/{id}/onvif/resolve-streams", onvif_resolve_streams_endpoint
    )
    app.router.add_get("/api/cameras/{id}/interop", camera_interop_endpoint)
    app.router.add_get("/api/system/interop", system_interop_endpoint)
    app.router.add_get("/api/docs/integration", integration_docs_endpoint)
    app.router.add_get("/api/docs/integration/openapi.json", integration_openapi_endpoint)
