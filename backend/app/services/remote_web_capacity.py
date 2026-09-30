"""RDSO 18.5(i) — remote web capacity: >=1000 users / >=100 concurrent logins.

Software evidence only by default. Heavy login soak is opt-in and must not
target production. Mongo-backed opaque sessions already allow concurrent clients.
"""

from __future__ import annotations

import os
from typing import Any, Optional

# Clause minima (software must meet or exceed — no lower hard caps).
RDSO_18_5_I_MIN_USERS = 1000
RDSO_18_5_I_MIN_CONCURRENT_LOGINS = 100

# Explicit: there is no software reject below these. Env soft caps are optional
# ops safety valves and must not default below RDSO minima if set.
def _optional_int_env(name: str) -> Optional[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def max_users_soft_cap() -> Optional[int]:
    """Optional ops soft cap (REMOTE_WEB_MAX_USERS). None = unlimited."""
    return _optional_int_env("REMOTE_WEB_MAX_USERS")


def max_concurrent_sessions_soft_cap() -> Optional[int]:
    """Optional ops soft cap (REMOTE_WEB_MAX_CONCURRENT_SESSIONS). None = unlimited."""
    return _optional_int_env("REMOTE_WEB_MAX_CONCURRENT_SESSIONS")


def _soft_cap_ok(cap: Optional[int], minimum: int) -> bool:
    if cap is None:
        return True
    return cap >= minimum


async def count_users() -> int:
    from app.core.database import user_collection

    return int(await user_collection.count_documents({}))


async def count_active_sessions() -> int:
    from app.services.session_service import count_active_sessions as _count

    return int(await _count())


def sessions_architecture_public() -> dict[str, Any]:
    return {
        "store": "mongodb",
        "token_type": "opaque_cookie",
        "cookie_name": os.getenv("SESSION_COOKIE_NAME", "nvr_session"),
        "jwt": False,
        "concurrent_sessions_per_user_allowed": True,
        "shared_across_vms_ha_nodes": True,
        "no_single_session_revoke_on_login": True,
    }


async def get_rdso_18_5_i_capacity(*, include_counts: bool = True) -> dict[str, Any]:
    """Software capability + optional live Mongo counts (no login soak)."""
    users_cap = max_users_soft_cap()
    sessions_cap = max_concurrent_sessions_soft_cap()
    users_ok = _soft_cap_ok(users_cap, RDSO_18_5_I_MIN_USERS)
    logins_ok = _soft_cap_ok(sessions_cap, RDSO_18_5_I_MIN_CONCURRENT_LOGINS)

    user_count: Optional[int] = None
    session_count: Optional[int] = None
    if include_counts:
        try:
            user_count = await count_users()
        except Exception:
            user_count = None
        try:
            session_count = await count_active_sessions()
        except Exception:
            session_count = None

    return {
        "rdso_18_5_i": True,
        "min_users": RDSO_18_5_I_MIN_USERS,
        "min_concurrent_logins": RDSO_18_5_I_MIN_CONCURRENT_LOGINS,
        "users": {
            "min_required": RDSO_18_5_I_MIN_USERS,
            "hard_cap": None,
            "soft_cap": users_cap,
            "software_unlimited_by_default": users_cap is None,
            "software_compliant": users_ok,
            "current_count": user_count,
            "storage": "mongodb.users",
        },
        "concurrent_logins": {
            "min_required": RDSO_18_5_I_MIN_CONCURRENT_LOGINS,
            "hard_cap": None,
            "soft_cap": sessions_cap,
            "software_unlimited_by_default": sessions_cap is None,
            "software_compliant": logins_ok,
            "current_active_sessions": session_count,
            "storage": "mongodb.sessions",
        },
        "sessions": sessions_architecture_public(),
        "software_compliant": bool(users_ok and logins_ok),
        "acceptance": {
            "software_probe": "python backend/scripts/rdso_18_5_i_accept_remote_capacity.py",
            "concurrent_login_soak": (
                "Opt-in only: RDSO_18_5_I_ACCEPTANCE=1 "
                "python backend/scripts/rdso_18_5_i_accept_remote_capacity.py --login-soak 100 "
                "(local/test API only — do not load-test production)."
            ),
            "linux_wan_note": (
                "Site acceptance still needs Linux/WAN load with a dedicated Mongo "
                "and >=1000 provisioned users; this endpoint does not claim that soak."
            ),
        },
    }
