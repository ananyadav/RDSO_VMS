from aiohttp import web

from app.core.database import user_helper
from app.core.http_utils import read_json_body
from app.services.audit_service import ACTION_LOGIN_FAILED, ACTION_LOGIN_SUCCESS, ACTION_LOGOUT, write_audit
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
    attempted = (data.get("name") or data.get("username") or "").strip()
    result, status = await handle_login(data)
    if status != 200:
        reason = "invalid_credentials"
        if status == 403:
            reason = "account_disabled"
        elif status == 400:
            reason = "missing_credentials"
        await write_audit(
            action=ACTION_LOGIN_FAILED,
            actor=None,
            resource_type="auth",
            resource_label=attempted or None,
            request=request,
            success=False,
            status="failure",
            metadata={"internal_reason": reason},
        )
        return web.json_response(result, status=status)

    user_id = result.get("id")
    if not user_id:
        return web.json_response({"error": "Login response missing user id"}, status=500)

    token = await create_session(
        user_id,
        request=request,
        user={"_id": user_id, "name": result.get("name"), "username": result.get("name"), "role": result.get("role")},
    )
    await write_audit(
        action=ACTION_LOGIN_SUCCESS,
        actor={"_id": user_id, "name": result.get("name"), "username": result.get("name"), "role": result.get("role")},
        resource_type="auth",
        resource_id=user_id,
        resource_label=result.get("name"),
        request=request,
        success=True,
        metadata={"role": result.get("role")},
    )
    response = web.json_response(result, status=200)
    attach_session_cookie(response, request, token)
    return response


async def logout_endpoint(request):
    token = read_session_token(request)
    actor = request.get("auth_user")
    if token:
        await revoke_session(token, revoked_by=str(actor.get("_id")) if actor else None)
    await write_audit(
        action=ACTION_LOGOUT,
        actor=actor,
        resource_type="auth",
        resource_id=str(actor.get("_id")) if actor else None,
        request=request,
        success=True,
    )
    response = web.json_response({"ok": True})
    clear_session_cookie(response, request)
    return response


async def session_endpoint(request):
    """Return fresh user profile for the current client session."""
    user = request.get("auth_user")
    if not user:
        return web.json_response({"error": "Not authenticated"}, status=401)
    return web.json_response(user_helper(user))
