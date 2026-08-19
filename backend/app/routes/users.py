from aiohttp import web

from app.core.access_control import require_admin
from app.core.database import get_user_by_id, get_users, user_collection
from app.core.http_utils import read_json_body
from app.core.roles import normalize_role, parse_requested_role
from app.services.audit_service import (
    ACTION_USER_CREATED,
    ACTION_USER_DISABLED,
    ACTION_USER_ENABLED,
    ACTION_USER_PASSWORD_RESET,
    ACTION_USER_ROLE_CHANGED,
    ACTION_USER_UPDATED,
    AUDIT_INCOMPLETE_ERROR,
    commit_critical_audit,
    write_audit,
)
from app.services.user_rbac import (
    GENERIC_FORBIDDEN,
    can_create_role,
    can_delete_user,
    can_modify_user,
    is_concealed_from,
    visible_users,
)
from app.services.user_service import handle_add_user, handle_delete_user, handle_update_user


def _forbidden() -> web.Response:
    return web.json_response({"error": GENERIC_FORBIDDEN}, status=403)


def _not_found() -> web.Response:
    return web.json_response({"error": "User not found"}, status=404)


def _audit_incomplete() -> web.Response:
    return web.json_response({"error": AUDIT_INCOMPLETE_ERROR}, status=500)


async def _require_user_manager(request: web.Request):
    try:
        return await require_admin(request), None
    except web.HTTPUnauthorized:
        return None, web.json_response({"error": "Authentication required"}, status=401)
    except web.HTTPForbidden:
        return None, _forbidden()


async def get_users_list(request):
    actor, err = await _require_user_manager(request)
    if err is not None:
        return err
    users = await get_users()
    return web.json_response(visible_users(actor, users))


async def add_user_endpoint(request):
    actor, err = await _require_user_manager(request)
    if err is not None:
        return err
    user_data, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    requested_role = parse_requested_role(user_data.get("role") or "") or (user_data.get("role") or "")
    if requested_role:
        user_data["role"] = requested_role
    allowed, _reason = can_create_role(actor, requested_role)
    if not allowed:
        await write_audit(
            action=ACTION_USER_CREATED,
            actor=actor,
            resource_type="user",
            request=request,
            success=False,
            status="forbidden",
            metadata={"internal_reason": "role_not_permitted"},
        )
        return _forbidden()
    result, status = await handle_add_user(user_data)
    if status < 300:
        created_id = result.get("id")

        async def _compensate():
            if created_id:
                await handle_delete_user(str(created_id))

        ok = await commit_critical_audit(
            compensate=_compensate,
            action=ACTION_USER_CREATED,
            actor=actor,
            resource_type="user",
            resource_id=created_id,
            resource_label=result.get("name"),
            request=request,
            success=True,
            metadata={"role": result.get("role")},
        )
        if not ok:
            return _audit_incomplete()
    return web.json_response(result, status=status)


async def update_user_endpoint(request):
    actor, err = await _require_user_manager(request)
    if err is not None:
        return err
    user_id = request.match_info.get("id")
    target = await get_user_by_id(user_id)
    if not target or is_concealed_from(actor, target):
        if target and is_concealed_from(actor, target):
            await write_audit(
                action=ACTION_USER_UPDATED,
                actor=actor,
                resource_type="user",
                request=request,
                success=False,
                status="forbidden",
                metadata={"internal_reason": "hidden"},
            )
        return _not_found()
    user_data, json_err = await read_json_body(request)
    if json_err is not None:
        return json_err
    if "role" in user_data and user_data.get("role") is not None:
        user_data["role"] = parse_requested_role(user_data.get("role")) or user_data.get("role")
    allowed, _reason = can_modify_user(actor, target, user_data)
    if not allowed:
        await write_audit(
            action=ACTION_USER_UPDATED,
            actor=actor,
            resource_type="user",
            resource_id=str(target.get("_id")),
            request=request,
            success=False,
            status="forbidden",
            metadata={"internal_reason": "not_permitted"},
        )
        return _forbidden()

    previous_role = target.get("role")
    previous_status = (target.get("status") or "Active").strip().lower()
    password_set = bool((user_data.get("password") or "").strip())

    result, status = await handle_update_user(user_id, user_data)
    if status != 200:
        return web.json_response(result, status=status)

    new_status = (result.get("status") or "Active").strip().lower()
    new_role = result.get("role")
    role_changed = "role" in user_data and normalize_role(previous_role) != normalize_role(new_role)
    status_changed = "status" in user_data and previous_status != new_status
    if role_changed:
        action = ACTION_USER_ROLE_CHANGED
    elif status_changed:
        action = ACTION_USER_DISABLED if new_status == "disabled" else ACTION_USER_ENABLED
    elif password_set:
        action = ACTION_USER_PASSWORD_RESET
    else:
        action = ACTION_USER_UPDATED

    async def _compensate():
        await user_collection.replace_one({"_id": target["_id"]}, target)

    ok = await commit_critical_audit(
        compensate=_compensate,
        action=action,
        actor=actor,
        resource_type="user",
        resource_id=user_id,
        resource_label=result.get("name"),
        request=request,
        success=True,
        changes=_user_changes(target, result, user_data),
        metadata={"password_changed": True} if password_set else {},
    )
    if not ok:
        return _audit_incomplete()
    return web.json_response(result, status=status)


def _user_changes(before: dict, after: dict, payload: dict) -> dict:
    fields = [k for k in ("name", "email", "status", "role") if k in payload]
    changes = {}
    for field in fields:
        left = before.get(field)
        right = after.get(field)
        if left != right:
            changes[field] = {"before": left, "after": right}
    if "permissions" in payload:
        changes["permissions"] = {
            "before": before.get("permissions") or [],
            "after": after.get("permissions") or [],
        }
    if (payload.get("password") or "").strip():
        changes["password_changed"] = True
    return changes


async def delete_user_endpoint(request):
    actor, err = await _require_user_manager(request)
    if err is not None:
        return err
    user_id = request.match_info.get("id")
    target = await get_user_by_id(user_id)
    if not target or is_concealed_from(actor, target):
        if target and is_concealed_from(actor, target):
            await write_audit(
                action="USER_DELETED",
                actor=actor,
                resource_type="user",
                request=request,
                success=False,
                status="forbidden",
                metadata={"internal_reason": "hidden"},
            )
        return _not_found()
    allowed, _reason = can_delete_user(actor, target)
    if not allowed:
        await write_audit(
            action="USER_DELETED",
            actor=actor,
            resource_type="user",
            resource_id=user_id,
            request=request,
            success=False,
            status="forbidden",
            metadata={"internal_reason": "not_permitted"},
        )
        return _forbidden()
    result, status = await handle_delete_user(user_id)
    if status == 204:
        async def _compensate():
            await user_collection.replace_one({"_id": target["_id"]}, target, upsert=True)

        ok = await commit_critical_audit(
            compensate=_compensate,
            action="USER_DELETED",
            actor=actor,
            resource_type="user",
            resource_id=user_id,
            resource_label=(target.get("username") or target.get("name") or ""),
            request=request,
            success=True,
        )
        if not ok:
            return _audit_incomplete()
        return web.json_response({}, status=204)
    return web.json_response(result, status=status)
