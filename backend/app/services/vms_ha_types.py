"""RDSO 18.1.29 — VMS management-server HA types (not recording HA)."""

from __future__ import annotations

from typing import Any, Literal

ROLE_PRIMARY = "primary"
ROLE_STANDBY = "standby"
VmsRole = Literal["primary", "standby"]

# Singleton coordination lease — only one VMS node runs critical background jobs.
LEASE_VMS_COORDINATOR = "vms_coordinator"

SINGLETON_LEASES = frozenset({LEASE_VMS_COORDINATOR})


def public_vms_node(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "vms_server_id": doc.get("vms_server_id") or doc.get("server_id"),
        "role": doc.get("role") or ROLE_PRIMARY,
        "enabled": bool(doc.get("enabled", True)),
        "healthy": bool(doc.get("healthy", False)),
        "online": bool(doc.get("healthy", False)) and bool(doc.get("enabled", True)),
        "is_leader": bool(doc.get("is_leader", False)),
        "last_seen": doc.get("last_seen"),
        "hostname": doc.get("hostname") or "",
        "state": doc.get("state") or ("leader" if doc.get("is_leader") else "follower"),
    }


def public_vms_lease(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        "lease_name": doc.get("lease_name"),
        "owner_vms_server_id": doc.get("owner_vms_server_id"),
        "lease_token": doc.get("lease_token"),
        "heartbeat_at": doc.get("heartbeat_at"),
        "expires_at": doc.get("expires_at"),
        "updated_at": doc.get("updated_at"),
    }
