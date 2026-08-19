"""Site / building / floor configuration API."""

import copy
import logging

from aiohttp import web

from app.core.auth_context import get_effective_user
from app.core.database import camera_collection
from app.services.audit_service import (
    ACTION_LOCATION_CREATED,
    ACTION_LOCATION_DELETED,
    ACTION_LOCATION_UPDATED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
)
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
    save_sites,
    update_building,
    update_floor,
    update_site,
)

logger = logging.getLogger(__name__)


def _audit_incomplete() -> web.Response:
    return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)


async def _critical_location_audit(*, before_sites, extra_cameras=None, **kwargs) -> web.Response | None:
    async def _compensate():
        if before_sites is not None:
            await save_sites(before_sites)
        for cam in extra_cameras or []:
            try:
                await camera_collection.replace_one({"_id": cam["_id"]}, cam, upsert=True)
            except Exception as exc:
                logger.critical("[audit] location camera restore failed: %s", exc)
        await sync_locations_catalog()

    ok = await commit_critical_audit(compensate=_compensate, **kwargs)
    if not ok:
        return _audit_incomplete()
    return None


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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        site = await add_site(name=(body.get("name") or "").strip())
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_CREATED,
            actor=user,
            resource_type="location",
            resource_id=str(site.get("id") or site.get("_id") or ""),
            resource_label=site.get("name") or site.get("site"),
            request=request,
            success=True,
            metadata={"kind": "site"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        site = await update_site(
            site_id=site_id,
            name=body.get("name"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_UPDATED,
            actor=user,
            resource_type="location",
            resource_id=site_id,
            resource_label=site.get("name") or site.get("site"),
            request=request,
            success=True,
            metadata={"kind": "site"},
        )
        if failed is not None:
            return failed
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"site": site})


async def delete_site_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    try:
        site_id = request.match_info.get("siteId", "")
        before = copy.deepcopy(await load_sites(include_inactive=True))
        site_name = ""
        for s in before:
            if str(s.get("id")) == str(site_id):
                site_name = (s.get("name") or "").strip()
                break
        extra = [doc async for doc in camera_collection.find({"site": site_name})] if site_name else []
        result = await delete_site(site_id=site_id)
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            extra_cameras=extra,
            action=ACTION_LOCATION_DELETED,
            actor=user,
            resource_type="location",
            resource_id=site_id,
            request=request,
            success=True,
            metadata={"kind": "site"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        entry = await add_building(site_id=site_id, building=building, floors=floors)
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_CREATED,
            actor=user,
            resource_type="location",
            resource_id=str(entry.get("id") or entry.get("_id") or ""),
            resource_label=entry.get("building") or entry.get("name") or building,
            request=request,
            success=True,
            metadata={"kind": "building"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        building = await update_building(
            site_id=body.get("site_id") or request.match_info.get("siteId", ""),
            building_id=request.match_info.get("buildingId", ""),
            name=body.get("name") or body.get("building"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_UPDATED,
            actor=user,
            resource_type="location",
            resource_id=request.match_info.get("buildingId", ""),
            resource_label=building.get("building") or building.get("name"),
            request=request,
            success=True,
            metadata={"kind": "building"},
        )
        if failed is not None:
            return failed
    except LocationStoreError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    return web.json_response({"building": building})


async def delete_building_endpoint(request: web.Request) -> web.Response:
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    site_id = request.rel_url.query.get("site_id") or request.match_info.get("siteId", "")
    building_id = request.match_info.get("buildingId", "")
    try:
        before = copy.deepcopy(await load_sites(include_inactive=True))
        site_name = ""
        building_name = ""
        for s in before:
            if str(s.get("id")) == str(site_id):
                site_name = (s.get("name") or "").strip()
                for b in s.get("buildings") or []:
                    if str(b.get("id")) == str(building_id):
                        building_name = (b.get("name") or "").strip()
                        break
        q: dict = {}
        if site_name:
            q["site"] = site_name
        if building_name:
            q["building"] = building_name
        extra = [doc async for doc in camera_collection.find(q)] if q else []
        result = await delete_building(
            site_id=site_id,
            building_id=building_id,
        )
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            extra_cameras=extra,
            action=ACTION_LOCATION_DELETED,
            actor=user,
            resource_type="location",
            resource_id=request.match_info.get("buildingId", ""),
            request=request,
            success=True,
            metadata={"kind": "building"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        entry = await add_floor(
            site_id=body.get("site_id") or "",
            building_id=body.get("building_id") or "",
            floor=(body.get("floor") or body.get("name") or "").strip(),
        )
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_CREATED,
            actor=user,
            resource_type="location",
            resource_id=str(entry.get("id") or entry.get("floor") or ""),
            resource_label=entry.get("floor") or entry.get("name"),
            request=request,
            success=True,
            metadata={"kind": "floor"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        floor = await update_floor(
            site_id=body.get("site_id") or "",
            building_id=body.get("building_id") or "",
            floor_name=floor_name,
            new_name=body.get("name") or body.get("floor"),
            is_active=body.get("is_active") if "is_active" in body else None,
        )
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            action=ACTION_LOCATION_UPDATED,
            actor=user,
            resource_type="location",
            resource_id=floor_name,
            resource_label=floor.get("floor") or floor.get("name") or floor_name,
            request=request,
            success=True,
            metadata={"kind": "floor"},
        )
        if failed is not None:
            return failed
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
        before = copy.deepcopy(await load_sites(include_inactive=True))
        extra = [doc async for doc in camera_collection.find({"floor": floor_name})] if floor_name else []
        result = await delete_floor(site_id=site_id, building_id=building_id, floor_name=floor_name)
        await sync_locations_catalog()
        failed = await _critical_location_audit(
            before_sites=before,
            extra_cameras=extra,
            action=ACTION_LOCATION_DELETED,
            actor=user,
            resource_type="location",
            resource_id=floor_name,
            request=request,
            success=True,
            metadata={"kind": "floor"},
        )
        if failed is not None:
            return failed
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
