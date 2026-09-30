"""RDSO 18.3.3 — recording-server HA types."""

from __future__ import annotations

from typing import Any, Literal

ROLE_PRIMARY = "primary"
ROLE_STANDBY = "standby"
ServerRole = Literal["primary", "standby"]

OWNERSHIP_ACTIVE = "active"
OWNERSHIP_PENDING_FAILBACK = "pending_failback"


def public_server(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "server_id": doc.get("server_id"),
        "role": doc.get("role") or ROLE_PRIMARY,
        "enabled": bool(doc.get("enabled", True)),
        "healthy": bool(doc.get("healthy", False)),
        "online": bool(doc.get("healthy", False)) and bool(doc.get("enabled", True)),
        "last_seen": doc.get("last_seen"),
        "hostname": doc.get("hostname") or "",
        "protects_primary_ids": list(doc.get("protects_primary_ids") or []),
        "active_ownership_count": int(doc.get("active_ownership_count") or 0),
        "metadata": dict(doc.get("metadata") or {}),
    }


def public_ownership(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "camera_id": doc.get("camera_id"),
        "home_server_id": doc.get("home_server_id"),
        "owner_server_id": doc.get("owner_server_id"),
        "lease_token": doc.get("lease_token"),
        "state": doc.get("state") or OWNERSHIP_ACTIVE,
        "heartbeat_at": doc.get("heartbeat_at"),
        "expires_at": doc.get("expires_at"),
        "recording_active": bool(doc.get("recording_active")),
        "failover": bool(doc.get("failover")),
        "failback_requested": bool(doc.get("failback_requested")),
        "updated_at": doc.get("updated_at"),
    }
