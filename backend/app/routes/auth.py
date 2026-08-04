from aiohttp import web

from app.core.database import user_helper
from app.core.http_utils import read_json_body
from app.services.session_service import (
    attach_session_cookie,
    clear_session_cookie,
    create_session,
    read_session_token,
    revoke_session,
)
from app.services.user_service import handle_login


async def login_endpoint(request):
    data, err = await read_json_body(request)
    if err is not None:
        return err
    result, status = await handle_login(data)
    if status != 200:
        return web.json_response(result, status=status)

    user_id = result.get("id")
    if not user_id:
        return web.json_response({"error": "Login response missing user id"}, status=500)

    token = await create_session(user_id)
    response = web.json_response(result, status=200)
    attach_session_cookie(response, request, token)
    return response


async def logout_endpoint(request):
    token = read_session_token(request)
    if token:
        await revoke_session(token)
    response = web.json_response({"ok": True})
    clear_session_cookie(response, request)
    return response


async def session_endpoint(request):
    """Return fresh user profile for the current client session."""
    user = request.get("auth_user")
    if not user:
        return web.json_response({"error": "Not authenticated"}, status=401)
    return web.json_response(user_helper(user))
