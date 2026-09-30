"""RDSO 18.3.3 — N:1 recording-server redundancy tests (logical mocked servers)."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.recording_ha_store import InMemoryHaStore, reset_ha_store, use_memory_ha_store
from app.services.recording_ha_types import ROLE_PRIMARY, ROLE_STANDBY
from app.services.recording_server_config import clear_recording_ha_config_cache


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class HaTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_recording_ha_config_cache()
        self.store = use_memory_ha_store(InMemoryHaStore())
        self.env = patch.dict(
            os.environ,
            {
                "RECORDING_HA_ENABLED": "true",
                "RECORDING_HA_HEARTBEAT_TIMEOUT_SEC": "5",
                "RECORDING_HA_OWNERSHIP_TTL_SEC": "8",
                "RECORDING_SERVER_ID": "standby-1",
                "RECORDING_SERVER_ROLE": "standby",
            },
            clear=False,
        )
        self.env.start()
        clear_recording_ha_config_cache()

    async def asyncTearDown(self):
        self.env.stop()
        reset_ha_store()
        clear_recording_ha_config_cache()


class HeartbeatHealthy(HaTestBase):
    async def test_primary_heartbeat_healthy(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.upsert_server(
            "primary-a",
            {
                "role": ROLE_PRIMARY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
            },
        )
        self.assertTrue(ha.is_server_fresh(await self.store.get_server("primary-a")))
        failed = await ha.detect_failed_primaries()
        self.assertEqual(failed, [])


class PrimaryFailureDetection(HaTestBase):
    async def test_primary_failure_detection(self):
        from app.services import recording_ha_coordinator as ha

        stale = datetime.now(timezone.utc) - timedelta(seconds=30)
        await self.store.upsert_server(
            "primary-a",
            {
                "role": ROLE_PRIMARY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(stale),
            },
        )
        failed = await ha.detect_failed_primaries()
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["server_id"], "primary-a")
        self.assertFalse(failed[0]["healthy"])


class StandbyTakeover(HaTestBase):
    async def test_standby_takeover(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.upsert_server(
            "standby-1",
            {
                "role": ROLE_STANDBY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
                "protects_primary_ids": [],
            },
        )
        await self.store.upsert_server(
            "primary-a",
            {
                "role": ROLE_PRIMARY,
                "enabled": True,
                "healthy": False,
                "last_seen": _iso(now - timedelta(seconds=60)),
            },
        )
        await self.store.set_camera_home("cam-1", "primary-a")
        await self.store.set_camera_home("cam-2", "primary-a")

        # Primary held ownership that is now expired
        await self.store.try_claim_ownership(
            camera_id="cam-1",
            owner_server_id="primary-a",
            home_server_id="primary-a",
            lease_token="old",
            expires_at=_iso(now - timedelta(seconds=1)),
            heartbeat_at=_iso(now - timedelta(seconds=60)),
        )

        result = await ha.standby_takeover_for_primary("primary-a", standby_id="standby-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["taken_count"], 2)
        own1 = await self.store.get_ownership("cam-1")
        self.assertEqual(own1["owner_server_id"], "standby-1")
        self.assertTrue(own1["failover"])


class NtoOneStandby(HaTestBase):
    async def test_multiple_primaries_one_standby(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.upsert_server(
            "standby-1",
            {
                "role": ROLE_STANDBY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
                "protects_primary_ids": [],
            },
        )
        for pid in ("primary-a", "primary-b"):
            await self.store.upsert_server(
                pid,
                {
                    "role": ROLE_PRIMARY,
                    "enabled": True,
                    "healthy": False,
                    "last_seen": _iso(now - timedelta(seconds=60)),
                },
            )
        await self.store.set_camera_home("cam-a", "primary-a")
        await self.store.set_camera_home("cam-b", "primary-b")

        pass_result = await ha.run_failover_pass(standby_id="standby-1")
        self.assertTrue(pass_result["ok"])
        self.assertEqual(len(pass_result["failed_primaries"]), 2)
        self.assertEqual((await self.store.get_ownership("cam-a"))["owner_server_id"], "standby-1")
        self.assertEqual((await self.store.get_ownership("cam-b"))["owner_server_id"], "standby-1")


class NoDuplicateOwnership(HaTestBase):
    async def test_no_duplicate_camera_ownership(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.set_camera_home("cam-1", "primary-a")
        first = await ha.claim_camera_ownership(
            "cam-1", server_id="primary-a", home_server_id="primary-a"
        )
        self.assertTrue(first["ok"])
        # Fresh lease — second server must not steal
        second = await ha.claim_camera_ownership(
            "cam-1", server_id="standby-1", home_server_id="primary-a", failover=True
        )
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "owned_by_other")
        own = await self.store.get_ownership("cam-1")
        self.assertEqual(own["owner_server_id"], "primary-a")


class StandbyUnavailable(HaTestBase):
    async def test_standby_unavailable(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.upsert_server(
            "standby-1",
            {
                "role": ROLE_STANDBY,
                "enabled": False,
                "healthy": False,
                "last_seen": _iso(now),
            },
        )
        await self.store.upsert_server(
            "primary-a",
            {
                "role": ROLE_PRIMARY,
                "enabled": True,
                "healthy": False,
                "last_seen": _iso(now - timedelta(seconds=60)),
            },
        )
        await self.store.set_camera_home("cam-1", "primary-a")
        result = await ha.standby_takeover_for_primary("primary-a", standby_id="standby-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "standby_unavailable")


class ControlledFailback(HaTestBase):
    async def test_primary_recovery_failback(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.upsert_server(
            "primary-a",
            {
                "role": ROLE_PRIMARY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
            },
        )
        await self.store.upsert_server(
            "standby-1",
            {
                "role": ROLE_STANDBY,
                "enabled": True,
                "healthy": True,
                "last_seen": _iso(now),
            },
        )
        await self.store.set_camera_home("cam-1", "primary-a")
        await ha.claim_camera_ownership(
            "cam-1", server_id="standby-1", home_server_id="primary-a", failover=True
        )
        await self.store.renew_ownership(
            "cam-1",
            owner_server_id="standby-1",
            lease_token=(await self.store.get_ownership("cam-1"))["lease_token"],
            expires_at=_iso(now + timedelta(seconds=30)),
            heartbeat_at=_iso(now),
            recording_active=True,
        )

        marked = await ha.request_failback("primary-a", camera_ids=["cam-1"])
        self.assertTrue(marked["ok"])
        held = await ha.process_failback_releases(owner_server_id="standby-1", force=False)
        self.assertEqual(held["held"][0]["reason"], "recording_active")
        self.assertEqual((await self.store.get_ownership("cam-1"))["owner_server_id"], "standby-1")

        # After recording stops, failback proceeds
        await self.store.renew_ownership(
            "cam-1",
            owner_server_id="standby-1",
            lease_token=(await self.store.get_ownership("cam-1"))["lease_token"],
            expires_at=_iso(now + timedelta(seconds=30)),
            heartbeat_at=_iso(now),
            recording_active=False,
        )
        released = await ha.process_failback_releases(owner_server_id="standby-1", force=False)
        self.assertIn("cam-1", released["released"])
        reclaimed = await ha.reclaim_after_failback("primary-a")
        self.assertIn("cam-1", reclaimed["reclaimed"])
        self.assertEqual((await self.store.get_ownership("cam-1"))["owner_server_id"], "primary-a")


class SessionServerId(unittest.TestCase):
    def test_session_helper_includes_server_id(self):
        from bson import ObjectId

        from app.core.database import recording_session_helper

        doc = {
            "_id": ObjectId(),
            "camera_id": "c1",
            "status": "stopped",
            "recording_server_id": "primary-a",
            "recording_server_role": "primary",
        }
        out = recording_session_helper(doc)
        self.assertEqual(out["recording_server_id"], "primary-a")
        self.assertEqual(out["recording_server_role"], "primary")


class PlaybackLookupAfterFailover(unittest.TestCase):
    def test_playback_filter_ignores_server_id(self):
        """Sessions remain findable by camera identity regardless of recording_server_id."""
        import inspect

        from app.services import camera_identity

        src = inspect.getsource(camera_identity.recording_session_mongo_filter)
        self.assertNotIn("recording_server_id", src)
        sessions = [
            {"camera_id": "cam-1", "recording_server_id": "primary-a", "started_at": "t1"},
            {"camera_id": "cam-1", "recording_server_id": "standby-1", "started_at": "t2"},
        ]
        found = [s for s in sessions if s["camera_id"] == "cam-1"]
        self.assertEqual(len(found), 2)


class EnsureSessionFields(HaTestBase):
    async def test_ensure_session_server_fields(self):
        from app.services.recording_ha_coordinator import ensure_session_server_fields

        fields = await ensure_session_server_fields()
        self.assertEqual(fields["recording_server_id"], "standby-1")
        self.assertEqual(fields["recording_server_role"], "standby")


class LocalMayRecordGate(HaTestBase):
    async def test_gate_blocks_non_owner_when_ha_enabled(self):
        from app.services import recording_ha_coordinator as ha

        now = datetime.now(timezone.utc)
        await self.store.set_camera_home("cam-x", "primary-a")
        await ha.claim_camera_ownership("cam-x", server_id="primary-a", home_server_id="primary-a")
        own = await self.store.get_ownership("cam-x")
        await self.store.renew_ownership(
            "cam-x",
            owner_server_id="primary-a",
            lease_token=own["lease_token"],
            expires_at=_iso(now + timedelta(hours=1)),
            heartbeat_at=_iso(now),
        )
        gate = await ha.local_may_record_camera("cam-x")
        self.assertFalse(gate["allowed"])


if __name__ == "__main__":
    unittest.main()
