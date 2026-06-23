from aiohttp import web
import asyncio
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
from app.core.auth_context import get_effective_user
from app.services.camera_access import is_admin, user_can_access_camera

logger = logging.getLogger(__name__)


def _schedule_go2rtc_reload() -> None:
    async def _reload():
        try:
            from app.services.go2rtc_service import GO2RTC_ENABLED, LIVE_PROVIDER, start_go2rtc

            if GO2RTC_ENABLED and LIVE_PROVIDER == "go2rtc":
                await start_go2rtc(reload=True)
        except Exception as exc:
            logger.warning("[go2rtc] Config reload after camera change failed: %s", exc)

    asyncio.create_task(_reload())


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
    result = await scan_cameras(request)
    return web.json_response(result)


async def add_camera_endpoint(request):
    camera_data = await request.json()
    import logging
    log_data = {k: ('***' if k == 'password' and v else v) for k, v in camera_data.items()}
    logging.info(f"Received camera data: {log_data}")
    result, status = await handle_add_camera(camera_data)
    if status < 400:
        _schedule_go2rtc_reload()
    return web.json_response(result, status=status)


async def import_cameras_endpoint(request):
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    payload = await request.json()
    result, status = await handle_import_cameras(payload)
    if status < 400:
        _schedule_go2rtc_reload()
    return web.json_response(result, status=status)


async def update_camera_endpoint(request):
    camera_id = request.match_info['id']
    user = await get_effective_user(request)
    if user is not None and not is_admin(user):
        if not user_can_access_camera(user, camera_id):
            return web.json_response({'error': 'Forbidden'}, status=403)

    camera_data = await request.json()
    result, status = await handle_update_camera(camera_id, camera_data)
    if status < 400:
        _schedule_go2rtc_reload()
    return web.json_response(result, status=status)


async def delete_camera_endpoint(request):
    camera_id = request.match_info['id']
    user = await get_effective_user(request)
    if user is not None and not is_admin(user):
        return web.json_response({'error': 'Forbidden'}, status=403)

    deleted = await delete_camera(camera_id)
    if deleted:
        _schedule_go2rtc_reload()
        return web.json_response({'message': 'Camera deleted'})
    return web.json_response({'error': 'Camera not found'}, status=404)


async def test_camera_stream_endpoint(request):
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    camera_id = request.match_info["id"]
    result = await test_camera_stream(camera_id)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def reload_group_go2rtc_endpoint(request):
    user = await get_effective_user(request)
    if not is_admin(user):
        return web.json_response({"error": "Admin only"}, status=403)
    group = (request.match_info.get("camera_group") or "").strip()
    if not group:
        return web.json_response({"error": "camera_group required"}, status=400)
    result = await reload_go2rtc_for_group(group)
    _schedule_go2rtc_reload()
    return web.json_response(result)
