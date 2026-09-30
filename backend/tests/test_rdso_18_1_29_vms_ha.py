"""RDSO 18.1.29 — VMS management-server N:1 redundancy (logical nodes; no multi-host claim)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from app.services.vms_ha_store import InMemoryVmsHaStore, reset_vms_ha_store, use_memory_vms_ha_store
from app.services.vms_ha_types import LEASE_VMS_COORDINATOR
from app.services.vms_server_config import clear_vms_ha_config_cache


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class VmsHaTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_vms_ha_config_cache()
        self.store = use_memory_vms_ha_store(InMemoryVmsHaStore())
        from app.services import vms_ha_coordinator as vms_ha

        vms_ha.reset_vms_ha_runtime()
        self.env = patch.dict(
            os.environ,
            {
                "VMS_HA_ENABLED": "true",
                "VMS_HA_HEARTBEAT_TIMEOUT_SEC": "5",
                "VMS_HA_LEADER_TTL_SEC": "8",
                "VMS_SERVER_ID": "vms-a",
                "VMS_SERVER_ROLE": "primary",
            },
            clear=False,
        )
        self.env.start()
        clear_vms_ha_config_cache()

    async def asyncTearDown(self):
        from app.services import vms_ha_coordinator as vms_ha

        await vms_ha.stop_vms_ha_loops()
        vms_ha.reset_vms_ha_runtime()
        self.env.stop()
        reset_vms_ha_store()
        clear_vms_ha_config_cache()


class MultipleLogicalNodes(VmsHaTestBase):
    async def test_register_multiple_nodes(self):
        now = datetime.now(timezone.utc)
        await self.store.upsert_node(
            "vms-a",
            {
                "role": "primary",
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
                "state": "leader",
                "is_leader": True,
            },
        )
        await self.store.upsert_node(
            "vms-b",
            {
                "role": "standby",
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
                "state": "follower",
                "is_leader": False,
            },
        )
        from app.services import vms_ha_coordinator as vms_ha

        listed = await vms_ha.list_vms_nodes_public()
        self.assertEqual(listed["total"], 2)
        ids = {n["vms_server_id"] for n in listed["items"]}
        self.assertEqual(ids, {"vms-a", "vms-b"})


class HeartbeatFailureDetection(VmsHaTestBase):
    async def test_stale_node_marked_unhealthy(self):
        from app.services import vms_ha_coordinator as vms_ha

        stale = datetime.now(timezone.utc) - timedelta(seconds=60)
        await self.store.upsert_node(
            "vms-b",
            {
                "role": "standby",
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(stale),
            },
        )
        self.assertFalse(vms_ha.is_server_fresh(await self.store.get_node("vms-b")))
        await vms_ha.refresh_node_health_flags()
        doc = await self.store.get_node("vms-b")
        self.assertFalse(doc["healthy"])


class SingleLeaderLease(VmsHaTestBase):
    async def test_only_one_leader(self):
        from app.services import vms_ha_coordinator as vms_ha

        a = await vms_ha.claim_or_renew_coordinator_lease()
        self.assertTrue(a["leader"])
        lease = await self.store.get_lease(LEASE_VMS_COORDINATOR)
        self.assertEqual(lease["owner_vms_server_id"], "vms-a")

        with patch.dict(os.environ, {"VMS_SERVER_ID": "vms-b"}, clear=False):
            clear_vms_ha_config_cache()
            # Simulate second process with separate module leadership vars
            vms_ha.reset_vms_ha_runtime()
            b = await vms_ha.claim_or_renew_coordinator_lease()
            self.assertFalse(b["leader"])
            lease2 = await self.store.get_lease(LEASE_VMS_COORDINATOR)
            self.assertEqual(lease2["owner_vms_server_id"], "vms-a")


class StandbyTakeoverAfterTimeout(VmsHaTestBase):
    async def test_standby_acquires_expired_lease(self):
        from app.services import vms_ha_coordinator as vms_ha

        now = datetime.now(timezone.utc)
        await self.store.try_claim_lease(
            lease_name=LEASE_VMS_COORDINATOR,
            owner_vms_server_id="vms-a",
            lease_token="old",
            expires_at=_iso(now - timedelta(seconds=1)),
            heartbeat_at=_iso(now - timedelta(seconds=60)),
        )

        with patch.dict(
            os.environ,
            {"VMS_SERVER_ID": "vms-b", "VMS_SERVER_ROLE": "standby"},
            clear=False,
        ):
            clear_vms_ha_config_cache()
            vms_ha.reset_vms_ha_runtime()
            result = await vms_ha.claim_or_renew_coordinator_lease()
            self.assertTrue(result["leader"])
            lease = await self.store.get_lease(LEASE_VMS_COORDINATOR)
            self.assertEqual(lease["owner_vms_server_id"], "vms-b")


class NoSplitBrain(VmsHaTestBase):
    async def test_active_lease_blocks_steal(self):
        now = datetime.now(timezone.utc)
        await self.store.try_claim_lease(
            lease_name=LEASE_VMS_COORDINATOR,
            owner_vms_server_id="vms-a",
            lease_token="tok-a",
            expires_at=_iso(now + timedelta(seconds=60)),
            heartbeat_at=_iso(now),
        )
        stolen = await self.store.try_claim_lease(
            lease_name=LEASE_VMS_COORDINATOR,
            owner_vms_server_id="vms-b",
            lease_token="tok-b",
            expires_at=_iso(now + timedelta(seconds=60)),
            heartbeat_at=_iso(now),
        )
        self.assertIsNone(stolen)
        cur = await self.store.get_lease(LEASE_VMS_COORDINATOR)
        self.assertEqual(cur["owner_vms_server_id"], "vms-a")


class SingletonCallbacks(VmsHaTestBase):
    async def test_start_stop_on_leadership(self):
        from app.services import vms_ha_coordinator as vms_ha

        started = {"n": 0}
        stopped = {"n": 0}

        async def start():
            started["n"] += 1

        async def stop():
            stopped["n"] += 1

        vms_ha._start_singletons = start
        vms_ha._stop_singletons = stop
        await vms_ha._apply_leadership(True)
        self.assertEqual(started["n"], 1)
        await vms_ha._apply_leadership(False)
        self.assertEqual(stopped["n"], 1)


class SessionSurvivesNodeChange(unittest.TestCase):
    def test_sessions_are_mongo_backed(self):
        import inspect

        from app.services import session_service

        src = inspect.getsource(session_service)
        self.assertIn("sessions", src)
        self.assertIn("SESSION_COLLECTION", src)
        self.assertIn("database.get_collection", inspect.getsource(session_service))


class HaDisabledRunsLocally(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_vms_ha_config_cache()
        use_memory_vms_ha_store(InMemoryVmsHaStore())
        from app.services import vms_ha_coordinator as vms_ha

        vms_ha.reset_vms_ha_runtime()
        self.env = patch.dict(os.environ, {"VMS_HA_ENABLED": "false", "VMS_SERVER_ID": "solo"}, clear=False)
        self.env.start()
        clear_vms_ha_config_cache()

    async def asyncTearDown(self):
        from app.services import vms_ha_coordinator as vms_ha

        await vms_ha.stop_vms_ha_loops()
        vms_ha.reset_vms_ha_runtime()
        self.env.stop()
        reset_vms_ha_store()
        clear_vms_ha_config_cache()

    async def test_local_is_coordinator_when_ha_off(self):
        from app.services import vms_ha_coordinator as vms_ha

        self.assertTrue(await vms_ha.local_is_coordinator())
        started = {"n": 0}

        async def start():
            started["n"] += 1

        await vms_ha.start_vms_ha_loops(start_singletons=start, stop_singletons=None)
        self.assertEqual(started["n"], 1)


class NvrHaStillSeparate(unittest.TestCase):
    def test_recording_ha_routes_unchanged(self):
        from app.routes import recording_ha, vms_ha

        self.assertTrue(hasattr(recording_ha, "setup_recording_ha_routes"))
        self.assertTrue(hasattr(vms_ha, "setup_vms_ha_routes"))
        # Paths must not collide
        import inspect

        rec_src = inspect.getsource(recording_ha.setup_recording_ha_routes)
        vms_src = inspect.getsource(vms_ha.setup_vms_ha_routes)
        self.assertIn("/api/recordings/ha/", rec_src)
        self.assertIn("/api/vms/ha/", vms_src)
        self.assertNotIn("/api/recordings/ha/", vms_src)


class ClientServerIndependent(unittest.TestCase):
    def test_no_vms_node_urls_in_frontend_api_helpers(self):
        """Relative API paths — no VMS_SERVER_ID baked into media URLs."""
        from pathlib import Path

        frontend = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
        bad = []
        for path in frontend.glob("*.ts"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "VMS_SERVER_ID" in text:
                bad.append(path.name)
        self.assertEqual(bad, [])


class RbacVmsHaRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_status_requires_super_admin(self):
        from app.routes import vms_ha as routes

        denied = web.json_response({"error": "Forbidden"}, status=403)
        with patch(
            "app.routes.vms_ha.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=denied,
        ):
            resp = await routes.vms_ha_status_endpoint(MagicMock())
            self.assertEqual(resp.status, 403)

    async def test_ready_is_public(self):
        from app.core.auth_context import _PUBLIC_API_PREFIXES

        self.assertIn("/api/vms/ha/ready", _PUBLIC_API_PREFIXES)


class HealthIncludesVms(unittest.TestCase):
    def test_health_snapshot_keys(self):
        from app.core.startup_state import _vms_health_snapshot

        snap = _vms_health_snapshot()
        self.assertIn("vms_server_id", snap)
        self.assertIn("vms_ha_enabled", snap)
        self.assertIn("vms_local_is_leader", snap)


if __name__ == "__main__":
    unittest.main()
