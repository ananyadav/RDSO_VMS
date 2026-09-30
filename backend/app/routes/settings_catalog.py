"""RDSO 18.1.11.8 / 18.1.18 — settings catalog API (Admin+)."""

from __future__ import annotations

from aiohttp import web

from app.core.access_control import deny_unless_admin
from app.core.database import camera_collection
from app.services import recording_schedule_store as recording_sched
from app.services.settings_catalog import build_settings_catalog, filter_catalog


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


async def settings_catalog_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    camera_count = await camera_collection.count_documents({})
    catalog = build_settings_catalog(
        camera_count=int(camera_count),
        master_enabled=bool(recording_sched.master_enabled),
    )
    q = request.rel_url.query
    filtered = filter_catalog(
        catalog,
        scope=q.get("scope"),
        editable=_bool_query(q.get("editable")),
    )
    return web.json_response(filtered)


def setup_settings_catalog_routes(app: web.Application) -> None:
    app.router.add_get("/api/settings/catalog", settings_catalog_endpoint)
