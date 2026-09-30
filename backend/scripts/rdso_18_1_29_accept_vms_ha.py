#!/usr/bin/env python3
"""Logical acceptance probe for RDSO 18.1.29 VMS N:1 (does NOT claim multi-host).

Runs in-process lease/heartbeat checks against the in-memory store.
Physical multi-host + shared Mongo + LB still required for site acceptance.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("VMS_HA_ENABLED", "true")
os.environ.setdefault("VMS_SERVER_ID", "vms-accept-a")
os.environ.setdefault("VMS_HA_HEARTBEAT_TIMEOUT_SEC", "5")
os.environ.setdefault("VMS_HA_LEADER_TTL_SEC", "8")


async def main() -> int:
    from app.services.vms_ha_store import InMemoryVmsHaStore, reset_vms_ha_store, use_memory_vms_ha_store
    from app.services.vms_ha_types import LEASE_VMS_COORDINATOR
    from app.services.vms_server_config import clear_vms_ha_config_cache
    from app.services import vms_ha_coordinator as vms_ha

    clear_vms_ha_config_cache()
    use_memory_vms_ha_store(InMemoryVmsHaStore())
    vms_ha.reset_vms_ha_runtime()

    print("RDSO 18.1.29 logical acceptance (single process, in-memory store)")
    print("NOTE: Does not prove physical multi-host failover.")

    claim = await vms_ha.claim_or_renew_coordinator_lease()
    assert claim["leader"], "primary should acquire lease"
    print("[OK] Leader lease acquired")

    # Second logical node cannot steal active lease
    os.environ["VMS_SERVER_ID"] = "vms-accept-b"
    clear_vms_ha_config_cache()
    vms_ha.reset_vms_ha_runtime()
    blocked = await vms_ha.claim_or_renew_coordinator_lease()
    assert not blocked["leader"], "second node must not split-brain"
    print("[OK] Split-brain blocked while lease active")

    # Expire and takeover
    from app.services.vms_ha_store import get_vms_ha_store

    store = get_vms_ha_store()
    now = datetime.now(timezone.utc)
    await store.try_claim_lease(
        lease_name=LEASE_VMS_COORDINATOR,
        owner_vms_server_id="vms-accept-a",
        lease_token="expired",
        expires_at=(now - timedelta(seconds=1)).isoformat(),
        heartbeat_at=(now - timedelta(seconds=60)).isoformat(),
    )
    takeover = await vms_ha.claim_or_renew_coordinator_lease()
    assert takeover["leader"], "standby should take expired lease"
    print("[OK] Standby takeover after TTL")

    # Recording HA remains a separate module
    from app.routes import recording_ha, vms_ha as vms_routes

    assert hasattr(recording_ha, "setup_recording_ha_routes")
    assert hasattr(vms_routes, "setup_vms_ha_routes")
    print("[OK] NVR HA (18.3.3) remains separate from VMS HA routes")

    await vms_ha.stop_vms_ha_loops()
    reset_vms_ha_store()
    clear_vms_ha_config_cache()
    print("PASS (logical only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
