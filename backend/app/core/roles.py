"""Canonical VMS roles. Comparisons belong on the backend."""

from __future__ import annotations

from typing import Optional

ROLE_SUPER_ADMIN = "SUPER_ADMIN"
ROLE_ADMIN = "Admin"
ROLE_OPERATOR = "Operator"
ROLE_VIEWER = "Viewer"  # legacy; preserved

_SUPER_ADMIN_ALIASES = frozenset({"super_admin", "superadmin", "super-admin", "super admin"})
_ADMIN_ALIASES = frozenset({"admin", "administrator"})
_OPERATOR_ALIASES = frozenset({"operator"})
_VIEWER_ALIASES = frozenset({"viewer"})


def normalize_role(user_or_role: Optional[object]) -> str:
    """Return a canonical role string, or '' if unknown."""
    if user_or_role is None:
        return ""
    if isinstance(user_or_role, dict):
        raw = user_or_role.get("role") or ""
    else:
        raw = user_or_role
    key = str(raw).strip().lower()
    if not key:
        return ""
    if key in _SUPER_ADMIN_ALIASES or key == "super_admin":
        return ROLE_SUPER_ADMIN
    if key in _ADMIN_ALIASES:
        return ROLE_ADMIN
    if key in _OPERATOR_ALIASES:
        return ROLE_OPERATOR
    if key in _VIEWER_ALIASES:
        return ROLE_VIEWER
    if key == "super_admin" or str(raw).strip() == ROLE_SUPER_ADMIN:
        return ROLE_SUPER_ADMIN
    return str(raw).strip()


def is_super_admin(user: Optional[dict]) -> bool:
    return normalize_role(user) == ROLE_SUPER_ADMIN


def is_ops_admin(user: Optional[dict]) -> bool:
    """Day-to-day CCTV admin: ADMIN or SUPER_ADMIN."""
    return normalize_role(user) in {ROLE_ADMIN, ROLE_SUPER_ADMIN}


def is_operator(user: Optional[dict]) -> bool:
    return normalize_role(user) == ROLE_OPERATOR


def stored_role_label(user: Optional[dict]) -> str:
    """Role string as stored / displayed (not guessed)."""
    if not user:
        return ""
    raw = (user.get("role") or "").strip()
    canonical = normalize_role(user)
    if canonical == ROLE_SUPER_ADMIN:
        return ROLE_SUPER_ADMIN
    return raw or canonical


def parse_requested_role(value: Optional[str]) -> str:
    """Canonicalize a role from API input. Unknown values returned stripped as-is."""
    return normalize_role(value) or (value or "").strip()


def is_protected_super_admin_role(role: Optional[str]) -> bool:
    return normalize_role(role) == ROLE_SUPER_ADMIN
