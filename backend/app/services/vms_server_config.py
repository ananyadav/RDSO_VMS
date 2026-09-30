"""RDSO 18.1.29 — VMS management-server identity / HA config.

Separate from recording-server HA (18.3.3 / RECORDING_*). Default off —
single-process deployments behave exactly as before.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def local_vms_server_id() -> str:
    explicit = (os.getenv("VMS_SERVER_ID") or "").strip()
    if explicit:
        return explicit
    host = socket.gethostname().strip() or "localhost"
    return f"vms-{host}"


def vms_ha_enabled() -> bool:
    """Multi-VMS coordination. Default off — every process runs singleton jobs locally."""
    return _env_bool("VMS_HA_ENABLED", False)


def vms_heartbeat_interval_seconds() -> float:
    return max(1.0, _env_float("VMS_HA_HEARTBEAT_INTERVAL_SEC", 5.0))


def vms_heartbeat_timeout_seconds() -> float:
    return max(3.0, _env_float("VMS_HA_HEARTBEAT_TIMEOUT_SEC", 15.0))


def vms_leader_ttl_seconds() -> float:
    timeout = vms_heartbeat_timeout_seconds()
    configured = _env_float("VMS_HA_LEADER_TTL_SEC", timeout + 5.0)
    return max(timeout + 1.0, configured)


def local_vms_role() -> str:
    role = (os.getenv("VMS_SERVER_ROLE") or "primary").strip().lower()
    if role in ("standby", "secondary", "backup"):
        return "standby"
    return "primary"


def clear_vms_ha_config_cache() -> None:
    local_vms_server_id.cache_clear()
