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


async def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    await SESSION_COLLECTION.insert_one(
        {
            "token": token,
            "user_id": ObjectId(user_id),
            "created_at": now,
            "expires_at": expires,
            "last_seen_at": now,
        }
    )
    return token


async def revoke_session(token: str) -> None:
    if not token:
        return
    await SESSION_COLLECTION.delete_one({"token": token})


async def revoke_sessions_for_user(user_id: str) -> int:
    try:
        oid = ObjectId(user_id)
    except Exception:
        return 0
    result = await SESSION_COLLECTION.delete_many({"user_id": oid})
    return int(result.deleted_count)


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
