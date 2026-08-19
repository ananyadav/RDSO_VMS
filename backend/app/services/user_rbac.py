"""User-management authorization. Fail closed. Never leak SUPER_ADMIN to ADMIN."""

from __future__ import annotations

from typing import Optional, Tuple

from app.core.roles import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
    is_super_admin,
    normalize_role,
)

GENERIC_FORBIDDEN = "Forbidden"

# Roles ADMIN may create/update (legacy Viewer included so existing operators keep working).
_ADMIN_MANAGEABLE = frozenset({ROLE_OPERATOR, ROLE_VIEWER})


def can_list_user(actor: dict, target: dict) -> bool:
    if is_super_admin(actor):
        return True
    if normalize_role(actor) != ROLE_ADMIN:
        return False
    return normalize_role(target) != ROLE_SUPER_ADMIN


def is_concealed_from(actor: dict, target: Optional[dict]) -> bool:
    """ADMIN/OPERATOR must not learn that a SUPER_ADMIN account exists."""
    if not actor or not target:
        return False
    return not can_list_user(actor, target)


def visible_users(actor: dict, users: list) -> list:
    return [u for u in users if can_list_user(actor, u)]


def _is_self(actor: dict, target: Optional[dict]) -> bool:
    if not actor or not target:
        return False
    return str(actor.get("_id")) == str(target.get("_id") or target.get("id"))


def can_create_role(actor: dict, requested_role: str) -> Tuple[bool, str]:
    role = normalize_role(requested_role) or (requested_role or "").strip()
    if is_super_admin(actor):
        if role in {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}:
            return True, ""
        # Unknown legacy role: SUPER_ADMIN may still create it.
        return True, ""
    if normalize_role(actor) == ROLE_ADMIN:
        if role in _ADMIN_MANAGEABLE:
            return True, ""
        return False, GENERIC_FORBIDDEN
    return False, GENERIC_FORBIDDEN


def can_modify_user(actor: dict, target: dict, updates: dict) -> Tuple[bool, str]:
    if not actor or not target:
        return False, GENERIC_FORBIDDEN

    actor_role = normalize_role(actor)
    target_role = normalize_role(target)
    self_edit = _is_self(actor, target)

    new_role = None
    if "role" in updates and updates.get("role") is not None:
        new_role = normalize_role(updates.get("role")) or str(updates.get("role") or "").strip()

    if new_role and self_edit and new_role != actor_role:
        return False, GENERIC_FORBIDDEN

    if is_super_admin(actor):
        if self_edit and new_role and new_role != ROLE_SUPER_ADMIN:
            return False, GENERIC_FORBIDDEN
        return True, ""

    if actor_role != ROLE_ADMIN:
        return False, GENERIC_FORBIDDEN

    if target_role == ROLE_SUPER_ADMIN:
        return False, GENERIC_FORBIDDEN
    if target_role == ROLE_ADMIN:
        return False, GENERIC_FORBIDDEN
    if target_role not in _ADMIN_MANAGEABLE:
        return False, GENERIC_FORBIDDEN

    if new_role and new_role not in _ADMIN_MANAGEABLE:
        return False, GENERIC_FORBIDDEN
    if "permissions" in updates and target_role == ROLE_ADMIN:
        return False, GENERIC_FORBIDDEN
    return True, ""


def can_delete_user(actor: dict, target: dict) -> Tuple[bool, str]:
    if _is_self(actor, target):
        return False, GENERIC_FORBIDDEN
    if is_super_admin(actor):
        return True, ""
    if normalize_role(actor) != ROLE_ADMIN:
        return False, GENERIC_FORBIDDEN
    if normalize_role(target) in {ROLE_SUPER_ADMIN, ROLE_ADMIN}:
        return False, GENERIC_FORBIDDEN
    if normalize_role(target) not in _ADMIN_MANAGEABLE:
        return False, GENERIC_FORBIDDEN
    return True, ""
