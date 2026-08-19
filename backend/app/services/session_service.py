"""Server-side opaque sessions stored in MongoDB, delivered via HttpOnly cookie."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from aiohttp import web
from bson.objectid import ObjectId

from app.core.database import database, user_collection

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "nvr_session")
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "168"))  # 7 days
SESSION_COLLECTION = database.get_collection("sessions")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def ensure_session_indexes() -> None:
    try:
        await SESSION_COLLECTION.create_index("token", unique=True, name="idx_session_token")
        await SESSION_COLLECTION.create_index("user_id", name="idx_session_user_id")
        await SESSION_COLLECTION.create_index(
            "expires_at",
            expireAfterSeconds=0,
            name="idx_session_ttl",
        )
        await SESSION_COLLECTION.create_index("revoked_at", name="idx_session_revoked_at")
    except Exception as exc:
        logger.warning("[session] index setup: %s", exc)


def session_cookie_kwargs(request: web.Request, *, max_age: int) -> dict:
    secure_env = os.getenv("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
    secure = secure_env or request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    return {
        "max_age": max_age,
        "httponly": True,
        "secure": secure,
        "samesite": "Lax",
        "path": "/",
    }


async def create_session(user_id: str, request: Optional[web.Request] = None, user: Optional[dict] = None) -> str:
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    ip = ""
    agent = ""
    if request is not None:
        from app.services.request_meta import client_ip, user_agent as ua_of

        ip = client_ip(request)
        agent = ua_of(request)
    role = ""
    username = ""
    if user:
        from app.core.roles import stored_role_label

        role = stored_role_label(user)
        username = (user.get("username") or user.get("name") or "").strip()
    await SESSION_COLLECTION.insert_one(
        {
            "token": token,
            "user_id": ObjectId(user_id),
            "user_name": username,
            "role": role,
            "created_at": now,
            "expires_at": expires,
            "last_seen_at": now,
            "ip_address": ip,
            "user_agent": agent,
            "revoked_at": None,
            "revoked_by": None,
        }
    )
    return token


async def revoke_session(token: str, *, revoked_by: Optional[str] = None) -> None:
    if not token:
        return
    await SESSION_COLLECTION.update_one(
        {"token": token, "revoked_at": None},
        {"$set": {"revoked_at": _utcnow(), "revoked_by": revoked_by}},
    )


async def revoke_sessions_for_user(user_id: str, *, revoked_by: Optional[str] = None) -> int:
    count, _ids = await revoke_sessions_for_user_tracked(user_id, revoked_by=revoked_by)
    return count


async def revoke_sessions_for_user_tracked(
    user_id: str, *, revoked_by: Optional[str] = None
) -> tuple[int, list]:
    try:
        oid = ObjectId(user_id)
    except Exception:
        return 0, []
    ids = [doc["_id"] async for doc in SESSION_COLLECTION.find({"user_id": oid, "revoked_at": None}, {"_id": 1})]
    if not ids:
        return 0, []
    result = await SESSION_COLLECTION.update_many(
        {"_id": {"$in": ids}, "revoked_at": None},
        {"$set": {"revoked_at": _utcnow(), "revoked_by": revoked_by}},
    )
    return int(result.modified_count), ids


async def restore_sessions(session_ids: list) -> None:
    if not session_ids:
        return
    await SESSION_COLLECTION.update_many(
        {"_id": {"$in": session_ids}},
        {"$set": {"revoked_at": None, "revoked_by": None}},
    )


def _read_session_token(request) -> str:
    cookie = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie:
        return cookie
    # Legacy WebSocket / media fallback during migration
    return (request.query.get("session") or "").strip()


def read_session_token(request) -> str:
    return _read_session_token(request)


async def resolve_session_user(request) -> Tuple[Optional[dict], bool]:
    """
    Resolve user from opaque session cookie.

    Returns (user, stale_cookie):
    - No cookie → (None, False)
    - Valid session → (user_doc, False)
    - Expired / unknown token → (None, True) — caller should clear cookie, not 401
    """
    token = _read_session_token(request)
    if not token:
        return None, False

    doc = await SESSION_COLLECTION.find_one({"token": token})
    if not doc:
        return None, True
    if doc.get("revoked_at"):
        return None, True

    expires = _as_utc(doc.get("expires_at"))
    if expires is None or expires <= _utcnow():
        await SESSION_COLLECTION.delete_one({"_id": doc["_id"]})
        return None, True

    try:
        user = await user_collection.find_one({"_id": doc["user_id"]})
    except Exception:
        user = None
    if not user:
        await SESSION_COLLECTION.delete_one({"_id": doc["_id"]})
        return None, True

    await SESSION_COLLECTION.update_one(
        {"_id": doc["_id"]},
        {"$set": {"last_seen_at": _utcnow()}},
    )
    return user, False


def _public_session(doc: dict) -> dict:
    created = _as_utc(doc.get("created_at"))
    expires = _as_utc(doc.get("expires_at"))
    last_seen = _as_utc(doc.get("last_seen_at"))
    revoked = _as_utc(doc.get("revoked_at"))
    return {
        "id": str(doc.get("_id")),
        "user_id": str(doc.get("user_id") or ""),
        "user_name": doc.get("user_name") or "",
        "role": doc.get("role") or "",
        "created_at": created.isoformat() if created else None,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "expires_at": expires.isoformat() if expires else None,
        "ip_address": doc.get("ip_address") or "",
        "user_agent": doc.get("user_agent") or "",
        "revoked": bool(doc.get("revoked_at")),
        "revoked_at": revoked.isoformat() if revoked else None,
        "active": not doc.get("revoked_at") and expires is not None and expires > _utcnow(),
    }


async def list_sessions(
    *,
    user_id: Optional[str] = None,
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    query: dict[str, Any] = {}
    if user_id:
        try:
            query["user_id"] = ObjectId(user_id)
        except Exception:
            query["user_id"] = user_id
    if active_only:
        query["revoked_at"] = None
        query["expires_at"] = {"$gt": _utcnow()}
    total = await SESSION_COLLECTION.count_documents(query)
    cursor = SESSION_COLLECTION.find(query, {"token": 0}).sort("created_at", -1).skip(offset).limit(limit)
    items = [_public_session(doc) async for doc in cursor]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def attach_session_cookie(response: web.Response, request: web.Request, token: str) -> None:
    max_age = SESSION_TTL_HOURS * 3600
    response.set_cookie(SESSION_COOKIE_NAME, token, **session_cookie_kwargs(request, max_age=max_age))


def clear_session_cookie(response: web.StreamResponse, request: web.Request) -> None:
    try:
        response.del_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=session_cookie_kwargs(request, max_age=0)["secure"],
        )
    except Exception as exc:
        logger.debug("[session] could not clear cookie: %s", exc)
