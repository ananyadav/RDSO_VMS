"""RDSO 18.3.3 — recording-server HA configuration.

RDSO 18.1.17 — The VMS must not limit the number of networked Video Recording
Servers. The registry is unbounded; there is no MAX_RECORDING_SERVERS soft-cap.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache

# Explicit sentinel: registry accepts arbitrary server entries (18.1.17).
RECORDING_SERVER_COUNT_LIMIT = None


def recording_server_count_limit() -> None:
    """Always None — no artificial cap on registered recording servers."""
    return RECORDING_SERVER_COUNT_LIMIT


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
def local_recording_server_id() -> str:
    explicit = (os.getenv("RECORDING_SERVER_ID") or "").strip()
    if explicit:
        return explicit
    host = socket.gethostname().strip() or "localhost"
    return f"rec-{host}"


def recording_ha_enabled() -> bool:
    """Multi-server coordination. Default off — single local process stamps server_id only."""
    return _env_bool("RECORDING_HA_ENABLED", False)


def heartbeat_interval_seconds() -> float:
    return max(1.0, _env_float("RECORDING_HA_HEARTBEAT_INTERVAL_SEC", 5.0))


def heartbeat_timeout_seconds() -> float:
    """Bounded loss detection: primary considered offline after this many seconds without heartbeat."""
    return max(3.0, _env_float("RECORDING_HA_HEARTBEAT_TIMEOUT_SEC", 15.0))


def ownership_ttl_seconds() -> float:
    """Exclusive camera ownership lease TTL (must be > heartbeat interval)."""
    timeout = heartbeat_timeout_seconds()
    configured = _env_float("RECORDING_HA_OWNERSHIP_TTL_SEC", timeout + 5.0)
    return max(timeout + 1.0, configured)


def local_server_role() -> str:
    role = (os.getenv("RECORDING_SERVER_ROLE") or "primary").strip().lower()
    if role in ("standby", "secondary", "backup"):
        return "standby"
    return "primary"


def clear_recording_ha_config_cache() -> None:
    local_recording_server_id.cache_clear()
