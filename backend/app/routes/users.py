from aiohttp import web

from app.core.access_control import require_admin
from app.core.database import get_users
from app.services.user_service import handle_add_user, handle_update_user, handle_delete_user


async def get_users_list(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    users = await get_users()
    return web.json_response(users)


async def add_user_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    user_data = await request.json()
    result, status = await handle_add_user(user_data)
    return web.json_response(result, status=status)


async def update_user_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    user_id = request.match_info.get('id')
    user_data = await request.json()
    result, status = await handle_update_user(user_id, user_data)
    return web.json_response(result, status=status)


async def delete_user_endpoint(request):
    try:
        await require_admin(request)
    except web.HTTPUnauthorized:
        return web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return web.json_response({"error": "Admin only"}, status=403)
    user_id = request.match_info.get('id')
    result, status = await handle_delete_user(user_id)
    return web.json_response(result, status=status)
