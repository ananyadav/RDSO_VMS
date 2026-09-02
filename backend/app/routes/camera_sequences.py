"""Camera sequence CRUD — ADMIN write, Live View read with camera ACL."""

from aiohttp import web

from app.core.access_control import deny_unless_admin, deny_unless_admin_or_live_view
from app.core.auth_context import get_effective_user
from app.core.http_utils import read_json_body
from app.services.audit_service import (
    ACTION_CAMERA_SEQUENCE_CREATED,
    ACTION_CAMERA_SEQUENCE_DELETED,
    ACTION_CAMERA_SEQUENCE_UPDATED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
    field_diff,
)
from app.services.camera_sequence_service import (
    CameraSequenceValidationError,
    create_camera_sequence,
    delete_camera_sequence,
    get_camera_sequence,
    get_sequence_doc,
    list_camera_sequences,
    sequence_to_public,
    update_camera_sequence,
)


def _bool_query(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    return None


def _audit_incomplete() -> web.Response:
    return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)


def _sequence_audit_metadata(public: dict) -> dict:
    return {
        "camera_count": len(public.get("camera_ids") or []),
    }


async def list_camera_sequences_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin_or_live_view(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    q = request.rel_url.query
    try:
        limit = int(q.get("limit") or 100)
        offset = int(q.get("offset") or 0)
    except ValueError:
        return web.json_response({"error": "Invalid pagination"}, status=400)
    enabled = _bool_query(q.get("enabled"))
    data = await list_camera_sequences(user, enabled=enabled, limit=limit, offset=offset)
    return web.json_response(data)


async def get_camera_sequence_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin_or_live_view(request)
    if denied is not None:
        return denied
    user = await get_effective_user(request)
    sequence_id = request.match_info.get("id") or ""
    sequence = await get_camera_sequence(sequence_id, user)
    if not sequence:
        return web.json_response({"error": "Camera sequence not found"}, status=404)
    return web.json_response(sequence)


async def create_camera_sequence_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    payload, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    try:
        created = await create_camera_sequence(
            payload,
            created_by=str(actor.get("_id") or actor.get("id") or ""),
        )
    except CameraSequenceValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    async def _compensate():
        await delete_camera_sequence(created["id"])

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_CAMERA_SEQUENCE_CREATED,
        actor=actor,
        resource_type="camera_sequence",
        resource_id=created["id"],
        resource_label=created.get("name"),
        request=request,
        success=True,
        metadata=_sequence_audit_metadata(created),
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(created, status=201)


async def update_camera_sequence_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    sequence_id = request.match_info.get("id") or ""
    before_doc = await get_sequence_doc(sequence_id)
    if not before_doc:
        return web.json_response({"error": "Camera sequence not found"}, status=404)

    payload, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    try:
        updated = await update_camera_sequence(sequence_id, payload)
    except CameraSequenceValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not updated:
        return web.json_response({"error": "Camera sequence not found"}, status=404)

    before_public = sequence_to_public(before_doc, user={"role": "Admin"})
    changes = field_diff(before_public, updated, list(payload.keys()))

    async def _compensate():
        from app.core.database import camera_sequences_collection
        from bson import ObjectId

        await camera_sequences_collection.replace_one({"_id": ObjectId(sequence_id)}, before_doc)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_CAMERA_SEQUENCE_UPDATED,
        actor=actor,
        resource_type="camera_sequence",
        resource_id=sequence_id,
        resource_label=updated.get("name"),
        request=request,
        success=True,
        changes=changes,
        metadata=_sequence_audit_metadata(updated),
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(updated)


async def delete_camera_sequence_endpoint(request: web.Request) -> web.Response:
    denied = await deny_unless_admin(request)
    if denied is not None:
        return denied
    actor = await get_effective_user(request)
    sequence_id = request.match_info.get("id") or ""
    before_doc = await get_sequence_doc(sequence_id)
    if not before_doc:
        return web.json_response({"error": "Camera sequence not found"}, status=404)

    before_public = sequence_to_public(before_doc, user={"role": "Admin"})
    deleted = await delete_camera_sequence(sequence_id)
    if not deleted:
        return web.json_response({"error": "Camera sequence not found"}, status=404)

    async def _compensate():
        from app.core.database import camera_sequences_collection

        await camera_sequences_collection.replace_one({"_id": before_doc["_id"]}, before_doc, upsert=True)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=ACTION_CAMERA_SEQUENCE_DELETED,
        actor=actor,
        resource_type="camera_sequence",
        resource_id=sequence_id,
        resource_label=before_doc.get("name"),
        request=request,
        success=True,
        metadata=_sequence_audit_metadata(before_public),
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response({}, status=204)


def setup_camera_sequence_routes(app: web.Application) -> None:
    app.router.add_get("/api/camera-sequences", list_camera_sequences_endpoint)
    app.router.add_post("/api/camera-sequences", create_camera_sequence_endpoint)
    app.router.add_get("/api/camera-sequences/{id}", get_camera_sequence_endpoint)
    app.router.add_put("/api/camera-sequences/{id}", update_camera_sequence_endpoint)
    app.router.add_delete("/api/camera-sequences/{id}", delete_camera_sequence_endpoint)
