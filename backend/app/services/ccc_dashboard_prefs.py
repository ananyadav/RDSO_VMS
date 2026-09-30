"""RDSO 18.6.6 — per-user CCC dashboard layout preferences."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import database
from app.services.ccc_dashboard_service import DEFAULT_WIDGETS

prefs_collection = database.get_collection("ccc_dashboard_prefs")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _user_id(user: dict) -> str:
    return str(user.get("id") or user.get("_id") or "").strip()


def _normalize_widgets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        return [dict(w) for w in DEFAULT_WIDGETS]
    known = {w["id"]: dict(w) for w in DEFAULT_WIDGETS}
    out: list[dict[str, Any]] = []
    seen = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        wid = str(item.get("id") or "").strip()
        if not wid or wid not in known or wid in seen:
            continue
        seen.add(wid)
        base = known[wid]
        out.append(
            {
                "id": wid,
                "label": base["label"],
                "enabled": bool(item.get("enabled", True)),
                "order": int(item.get("order", i)),
            }
        )
    # Append any missing defaults (disabled) so schema stays stable.
    for w in DEFAULT_WIDGETS:
        if w["id"] not in seen:
            missing = dict(w)
            missing["enabled"] = False
            missing["order"] = len(out)
            out.append(missing)
    out.sort(key=lambda x: x["order"])
    for i, w in enumerate(out):
        w["order"] = i
    return out


async def ensure_dashboard_prefs_indexes() -> None:
    try:
        await prefs_collection.create_index(
            "user_id", unique=True, name="idx_ccc_dash_prefs_user"
        )
    except Exception:
        pass


async def get_dashboard_prefs(user: dict) -> dict[str, Any]:
    uid = _user_id(user)
    doc = await prefs_collection.find_one({"user_id": uid}) if uid else None
    widgets = _normalize_widgets((doc or {}).get("widgets"))
    return {
        "user_id": uid,
        "widgets": widgets,
        "updated_at": (doc or {}).get("updated_at").isoformat()
        if isinstance((doc or {}).get("updated_at"), datetime)
        else (doc or {}).get("updated_at"),
        "isolated": True,
        "rdso_18_6_6": True,
    }


async def save_dashboard_prefs(user: dict, *, widgets: Any) -> dict[str, Any]:
    uid = _user_id(user)
    if not uid:
        raise ValueError("Authenticated user id required")
    normalized = _normalize_widgets(widgets)
    now = _utcnow()
    await prefs_collection.update_one(
        {"user_id": uid},
        {
            "$set": {
                "user_id": uid,
                "widgets": normalized,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    return {
        "user_id": uid,
        "widgets": normalized,
        "updated_at": now.isoformat(),
        "isolated": True,
        "rdso_18_6_6": True,
    }
