"""Site / building / floor configuration API."""

import logging

from aiohttp import web

from app.core.auth_context import get_effective_user
from app.services.camera_access import is_admin
from app.services.location_catalog import sync_locations_catalog
from app.services.location_store import (
    LocationStoreError,
    add_building,
    add_floor,
    add_site,
    delete_building,
    delete_floor,
    delete_site,
    enrich_sites_with_camera_counts,
    flatten_buildings,
    list_buildings,
    load_sites,
    update_building,
    update_floor,
    update_site,
)

logger = logging.getLogger(__name__)


async def get_locations_endpoint(_request: web.Request) -> web.Response:
    """GET /api/locations — site → building → floor hierarchy."""
    include_inactive = (_request.rel_url.query.get("includeInactive") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    include_stats = (_request.rel_url.query.get("includeStats") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    sites = await load_sites(include_inactive=include_inactive)
    if include_stats:
        sites = await enrich_sites_with_camera_counts(sites)
    buildings = flatten_buildings(sites, include_inactive=include_inactive)
    return web.json_response({"sites": sites, "buildings": buildings})


async def post_site_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        site = await add_site(name=(body.get("name") or "").strip())
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"site": site}, status=201)


async def patch_site_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    site_id = request.match_info.get("siteId", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        site = await update_site(
            site_id=site_id,
            name=body.get("name"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"site": site})


async def delete_site_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        result = await delete_site(site_id=request.match_info.get("siteId", ""))
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"status": "ok", **result})


async def post_building_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    site_id = (body.get("site_id") or body.get("siteId") or "").strip()
    building = (body.get("building") or body.get("name") or "").strip()
    floors = body.get("floors") or []
    if isinstance(floors, str):
        floors = [line.strip() for line in floors.splitlines() if line.strip()]

    try:
        entry = await add_building(site_id=site_id, building=building, floors=floors)
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)

    return web.json_response({"building": entry}, status=201)


async def patch_building_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        building = await update_building(
            site_id=body.get("site_id") or request.match_info.get("siteId", ""),
            building_id=request.match_info.get("buildingId", ""),
            name=body.get("name") or body.get("building"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"building": building})


async def delete_building_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    site_id = request.rel_url.query.get("site_id") or request.match_info.get("siteId", "")
    try:
        result = await delete_building(
            site_id=site_id,
            building_id=request.match_info.get("buildingId", ""),
        )
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"status": "ok", **result})


async def post_floor_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        entry = await add_floor(
            site_id=body.get("site_id") or "",
            building_id=body.get("building_id") or "",
            floor=(body.get("floor") or body.get("name") or "").strip(),
        )
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)

    return web.json_response({"floor": entry})


async def patch_floor_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    floor_name = request.match_info.get("floorName", "")
    try:
        floor = await update_floor(
            site_id=body.get("site_id") or "",
            building_id=body.get("building_id") or "",
            floor_name=floor_name,
            new_name=body.get("name") or body.get("floor"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"floor": floor})


async def delete_floor_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    site_id = request.rel_url.query.get("site_id") or ""
    building_id = request.rel_url.query.get("building_id") or ""
    floor_name = request.match_info.get("floorName", "")
    try:
        result = await delete_floor(site_id=site_id, building_id=building_id, floor_name=floor_name)
        await sync_locations_catalog()
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"status": "ok", **result})


def setup_location_routes(app: web.Application) -> None:
    app.router.add_get("/api/locations", get_locations_endpoint)
    app.router.add_post("/api/locations/sites", post_site_endpoint)
    app.router.add_patch("/api/locations/sites/{siteId}", patch_site_endpoint)
    app.router.add_delete("/api/locations/sites/{siteId}", delete_site_endpoint)
    app.router.add_post("/api/locations/buildings", post_building_endpoint)
    app.router.add_patch(
        "/api/locations/sites/{siteId}/buildings/{buildingId}",
        patch_building_endpoint,
    )
    app.router.add_delete(
        "/api/locations/sites/{siteId}/buildings/{buildingId}",
        delete_building_endpoint,
    )
    app.router.add_post("/api/locations/floors", post_floor_endpoint)
    app.router.add_patch("/api/locations/floors/{floorName}", patch_floor_endpoint)
    app.router.add_delete("/api/locations/floors/{floorName}", delete_floor_endpoint)
