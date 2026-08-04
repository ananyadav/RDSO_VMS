from aiohttp import web
import logging

from app.services.camera_service import (
    get_camera_info,
    get_camera_groups,
    get_configured_cameras_for_user,
    scan_cameras,
    handle_add_camera,
    handle_import_cameras,
    handle_update_camera,
)
from app.services.camera_management import reload_go2rtc_for_group, test_camera_stream
from app.core.database import delete_camera
from app.core.access_control import require_admin
from app.services.camera_sync import schedule_camera_side_effects

logger = logging.getLogger(__name__)


def _schedule_go2rtc_reload() -> None:
    """Reload go2rtc after camera delete (add/update handled in camera_service)."""
    schedule_camera_side_effects("", existing=None, updated_fields=None, reason="camera_delete")


async def get_camera_list(request):
    cameras = await get_camera_info(request)
    return web.json_response(cameras)


async def get_camera_groups_endpoint(request):
    data = await get_camera_groups(request)
    return web.json_response(data)


async def get_configured_cameras(request):
    configured = await get_configured_cameras_for_user(request)
    return web.json_response(configured)


async def scan_for_cameras(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    result = await scan_cameras(request)
    return web.json_response(result)


async def add_camera_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    camera_data = await request.json()
    import logging
    log_data = {k: ('***' if k == 'password' and v else v) for k, v in camera_data.items()}
    logging.info(f"Received camera data: {log_data}")
    result, status = await handle_add_camera(camera_data)
    return web.json_response(result, status=status)


async def import_cameras_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    payload = await request.json()
    result, status = await handle_import_cameras(payload)
    return web.json_response(result, status=status)


async def update_camera_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    camera_id = request.match_info['id']
    camera_data = await request.json()
    result, status = await handle_update_camera(camera_id, camera_data)
    return web.json_response(result, status=status)


async def delete_camera_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    camera_id = request.match_info["id"]

    from bson import ObjectId
    from bson.errors import InvalidId
    from app.core.database import camera_collection

    try:
        existing = await camera_collection.find_one({"_id": ObjectId(camera_id)})
    except (InvalidId, TypeError):
        existing = None
    if not existing:
        return web.json_response({"error": "Camera not found"}, status=404)

    # Stop live recording and clear schedule flag before permanent delete.
    try:
        from app.services.recording_schedule_store import set_camera_recording
        from app.services.video_recording import is_camera_recording, stop_camera_recording

        if await is_camera_recording(camera_id):
            await stop_camera_recording(camera_id)
        set_camera_recording(camera_id, False)
    except Exception as exc:
        logger.warning("[cameras] Recording cleanup before delete failed for %s: %s", camera_id, exc)

    deleted = await delete_camera(camera_id)
    if not deleted:
        return web.json_response({"error": "Camera not found"}, status=404)

    # Sync the camera's worker so go2rtc drops its streams (DB row is already gone).
    try:
        from app.services.go2rtc_workers import (
            WORKERS_ENABLED,
            get_worker_id_for_camera_doc,
            sync_worker,
        )

        if WORKERS_ENABLED:
            wid = await get_worker_id_for_camera_doc(existing)
            await sync_worker(wid, reload_pm2=True)
        else:
            _schedule_go2rtc_reload()
    except Exception as exc:
        logger.warning("[cameras] go2rtc sync after delete failed for %s: %s", camera_id, exc)
        _schedule_go2rtc_reload()

    label = (existing.get("ip_address") or existing.get("name") or camera_id).strip()
    return web.json_response({"message": "Camera deleted", "cameraId": camera_id, "name": label})



async def test_camera_stream_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    camera_id = request.match_info["id"]
    result = await test_camera_stream(camera_id)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def reload_group_go2rtc_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    group = (request.match_info.get("camera_group") or "").strip()
    if not group:
        return web.json_response({"error": "camera_group required"}, status=400)
    result = await reload_go2rtc_for_group(group)
    _schedule_go2rtc_reload()
    return web.json_response(result)
