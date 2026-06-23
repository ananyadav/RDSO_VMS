from aiohttp import web

from app.core.database import user_helper
from app.services.user_service import handle_login


async def login_endpoint(request):
    data = await request.json()
    result, status = await handle_login(data)
    return web.json_response(result, status=status)


async def session_endpoint(request):
    """Return fresh user profile for the current client session."""
    uid = (request.headers.get("X-User-Id") or "").strip()
    if not uid:
        return web.json_response({"error": "Not authenticated"}, status=401)

    user = request.get("auth_user")
    if not user:
        return web.json_response(
            {"error": "Session invalid — user was deleted or access was revoked."},
            status=401,
        )
    return web.json_response(user_helper(user))
