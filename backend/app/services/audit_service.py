"""Append-only audit log. Never store secrets."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import database
from app.core.roles import stored_role_label
from app.services.rtsp_utils import mask_rtsp_url

logger = logging.getLogger(__name__)

AUDIT_COLLECTION = database.get_collection("audit_logs")

ACTION_LOGIN_SUCCESS = "LOGIN_SUCCESS"
ACTION_LOGIN_FAILED = "LOGIN_FAILED"
ACTION_LOGOUT = "LOGOUT"
ACTION_USER_CREATED = "USER_CREATED"
ACTION_USER_UPDATED = "USER_UPDATED"
ACTION_USER_DISABLED = "USER_DISABLED"
ACTION_USER_ENABLED = "USER_ENABLED"
ACTION_USER_PASSWORD_RESET = "USER_PASSWORD_RESET"
ACTION_USER_ROLE_CHANGED = "USER_ROLE_CHANGED"
ACTION_CAMERA_CREATED = "CAMERA_CREATED"
ACTION_CAMERA_UPDATED = "CAMERA_UPDATED"
ACTION_CAMERA_DELETED = "CAMERA_DELETED"
ACTION_LOCATION_CREATED = "LOCATION_CREATED"
ACTION_LOCATION_UPDATED = "LOCATION_UPDATED"
ACTION_LOCATION_DELETED = "LOCATION_DELETED"
ACTION_CAMERA_LOCATION_CHANGED = "CAMERA_LOCATION_CHANGED"
ACTION_PTZ_PAN = "PTZ_PAN"
ACTION_PTZ_TILT = "PTZ_TILT"
ACTION_PTZ_ZOOM = "PTZ_ZOOM"
ACTION_PTZ_STOP = "PTZ_STOP"
ACTION_SESSION_REVOKED = "SESSION_REVOKED"

_SECRET_KEYS = frozenset(
    {
        "password",
        "token",
        "nvr_session",
        "session",
        "mongodb_uri",
        "mongo_uri",
        "secret",
        "private_key",
        "api_key",
        "apikey",
    }
)
_RTSP_KEYS = frozenset({"rtsp_url", "main_rtsp_url", "sub_rtsp_url", "recording_rtsp_url"})
_LOCATION_KEYS = frozenset({"site", "building", "floor", "camera_group", "location_path"})
_RTSP_EMBEDDED = re.compile(r"(rtsp://)([^/@\s]+):([^/@\s]+)@", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_secret_field(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k == "password_changed":
        return False
    if k in _SECRET_KEYS:
        return True
    if k.endswith("_password") or k.endswith("password"):
        return True
    if k.endswith("_token") or k.endswith("_secret") or k.endswith("_key"):
        return True
    return False


def redact_value(key: str, value: Any) -> Any:
    k = (key or "").strip().lower()
    if _is_secret_field(key):
        return "[REDACTED]"
    if isinstance(value, (bytes, bytearray)):
        return "[REDACTED]"
    if isinstance(value, str):
        stripped = value.strip()
        low = stripped.lower()
        if low.startswith("mongodb://") or low.startswith("mongodb+srv://"):
            return "[REDACTED]"
        if stripped.startswith("$2a$") or stripped.startswith("$2b$") or stripped.startswith("$2y$"):
            return "[REDACTED]"
        if k in _RTSP_KEYS or low.startswith("rtsp://"):
            return mask_rtsp_url(_RTSP_EMBEDDED.sub(r"\1[REDACTED]@", stripped))
    elif k in _RTSP_KEYS:
        return "[REDACTED]"
    return value


def sanitize_changes(changes: Optional[dict]) -> dict:
    if not changes:
        return {}
    out: dict[str, Any] = {}
    for field, delta in changes.items():
        if not isinstance(delta, dict):
            out[field] = redact_value(field, delta)
            continue
        before = delta.get("before")
        after = delta.get("after")
        if _is_secret_field(field):
            out[field] = {"before": "[REDACTED]", "after": "[REDACTED]"}
            continue
        out[field] = {
            "before": redact_value(field, before),
            "after": redact_value(field, after),
        }
    return out


def field_diff(before: Optional[dict], after: Optional[dict], fields: list[str]) -> dict:
    before = before or {}
    after = after or {}
    changes: dict[str, Any] = {}
    for field in fields:
        left = before.get(field)
        right = after.get(field)
        if left == right:
            continue
        changes[field] = {"before": left, "after": right}
    return sanitize_changes(changes)


def location_fields_changed(before: Optional[dict], after: Optional[dict]) -> dict:
    return field_diff(before, after, list(_LOCATION_KEYS))


def sanitize_metadata(metadata: Optional[dict]) -> dict:
    if not metadata or not isinstance(metadata, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if _is_secret_field(str(key)):
            out[key] = "[REDACTED]"
        elif isinstance(value, dict):
            out[key] = sanitize_metadata(value)
        else:
            out[key] = redact_value(str(key), value)
    return out


def actor_fields(user: Optional[dict]) -> dict:
    if not user:
        return {
            "actor_user_id": None,
            "actor_username": None,
            "actor_role": None,
        }
    return {
        "actor_user_id": str(user.get("_id") or user.get("id") or ""),
        "actor_username": (user.get("username") or user.get("name") or "").strip() or None,
        "actor_role": stored_role_label(user) or None,
    }


class AuditWriteError(Exception):
    """Critical audit insert failed."""


AUDIT_INCOMPLETE_ERROR = "Operation could not be completed"


async def write_audit(
    *,
    action: str,
    actor: Optional[dict] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    resource_label: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    status: Optional[str] = None,
    changes: Optional[dict] = None,
    metadata: Optional[dict] = None,
    request=None,
    required: bool = False,
) -> bool:
    """Append an audit document.

    Best-effort by default (PTZ / LOGIN_FAILED): never raises, returns False on failure.
    Critical mutations pass required=True and receive AuditWriteError on failure.
    """
    try:
        from app.services.request_meta import client_ip, user_agent as ua_of

        ip = ip_address
        agent = user_agent
        if request is not None:
            ip = ip if ip is not None else client_ip(request)
            agent = agent if agent is not None else ua_of(request)
        doc = {
            "timestamp": _utcnow(),
            **actor_fields(actor),
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id is not None else None,
            "resource_label": resource_label,
            "ip_address": ip,
            "user_agent": agent,
            "success": bool(success),
            "status": status or ("success" if success else "failure"),
            "changes": sanitize_changes(changes) if changes else {},
            "metadata": sanitize_metadata(metadata),
        }
        result = await AUDIT_COLLECTION.insert_one(doc)
        if not getattr(result, "inserted_id", None):
            raise RuntimeError("audit insert returned no id")
        return True
    except Exception as exc:
        logger.warning("[audit] write failed action=%s: %s", action, exc)
        if required:
            raise AuditWriteError(str(exc)) from exc
        return False


async def commit_critical_audit(*, compensate=None, **kwargs) -> bool:
    """Write a required audit record. On failure, run compensate() if provided.

    Returns True when the audit document exists. Returns False after a failed
    insert (compensate is best-effort; a compensate failure is logged critically).
    """
    kwargs.pop("required", None)
    try:
        await write_audit(required=True, **kwargs)
        return True
    except AuditWriteError:
        if compensate is not None:
            try:
                await compensate()
            except Exception as exc:
                logger.critical("[audit] compensate failed action=%s: %s", kwargs.get("action"), exc)
        return False


async def ensure_audit_indexes() -> None:
    try:
        await AUDIT_COLLECTION.create_index("timestamp", name="idx_audit_timestamp")
        await AUDIT_COLLECTION.create_index(
            [("actor_user_id", 1), ("timestamp", -1)],
            name="idx_audit_actor_time",
        )
        await AUDIT_COLLECTION.create_index(
            [("action", 1), ("timestamp", -1)],
            name="idx_audit_action_time",
        )
        await AUDIT_COLLECTION.create_index(
            [("resource_type", 1), ("resource_id", 1), ("timestamp", -1)],
            name="idx_audit_resource_time",
        )
    except Exception as exc:
        logger.warning("[audit] index setup: %s", exc)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def query_audit_logs(
    *,
    actor_user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    success: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    query: dict[str, Any] = {}
    if actor_user_id:
        query["actor_user_id"] = str(actor_user_id).strip()
    if actor_role:
        query["actor_role"] = str(actor_role).strip()
    if action:
        query["action"] = str(action).strip()
    if resource_type:
        query["resource_type"] = str(resource_type).strip()
    if resource_id:
        query["resource_id"] = str(resource_id).strip()
    if success is not None:
        query["success"] = bool(success)
    rng: dict[str, Any] = {}
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt:
        rng["$gte"] = start_dt
    if end_dt:
        rng["$lte"] = end_dt
    if rng:
        query["timestamp"] = rng

    total = await AUDIT_COLLECTION.count_documents(query)
    cursor = (
        AUDIT_COLLECTION.find(query, {"_id": 1, "timestamp": 1, "actor_user_id": 1,
                                      "actor_username": 1, "actor_role": 1, "action": 1,
                                      "resource_type": 1, "resource_id": 1, "resource_label": 1,
                                      "ip_address": 1, "user_agent": 1, "success": 1, "status": 1,
                                      "changes": 1, "metadata": 1})
        .sort("timestamp", -1)
        .skip(offset)
        .limit(limit)
    )
    items = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        ts = doc.get("timestamp")
        if isinstance(ts, datetime):
            doc["timestamp"] = ts.astimezone(timezone.utc).isoformat()
        items.append(doc)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
