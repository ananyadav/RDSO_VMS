"""RDSO 18.5(i) — remote web capacity (>=1000 users / >=100 concurrent logins)."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services import remote_web_capacity as rwc
from app.services.remote_web_capacity import (
    RDSO_18_5_I_MIN_CONCURRENT_LOGINS,
    RDSO_18_5_I_MIN_USERS,
    get_rdso_18_5_i_capacity,
    max_concurrent_sessions_soft_cap,
    max_users_soft_cap,
    sessions_architecture_public,
)


class NoHardCaps(unittest.TestCase):
    def test_no_hard_cap_below_1000_users_by_default(self):
        self.assertIsNone(max_users_soft_cap())
        self.assertGreaterEqual(RDSO_18_5_I_MIN_USERS, 1000)

    def test_no_hard_cap_below_100_concurrent_logins_by_default(self):
        self.assertIsNone(max_concurrent_sessions_soft_cap())
        self.assertGreaterEqual(RDSO_18_5_I_MIN_CONCURRENT_LOGINS, 100)

    def test_soft_cap_below_minima_flags_noncompliant(self):
        with patch.dict(
            os.environ,
            {"REMOTE_WEB_MAX_USERS": "50", "REMOTE_WEB_MAX_CONCURRENT_SESSIONS": "10"},
        ):
            self.assertEqual(max_users_soft_cap(), 50)
            self.assertEqual(max_concurrent_sessions_soft_cap(), 10)
            cap = asyncio.run(get_rdso_18_5_i_capacity(include_counts=False))
            self.assertFalse(cap["software_compliant"])
            self.assertFalse(cap["users"]["software_compliant"])
            self.assertFalse(cap["concurrent_logins"]["software_compliant"])


class MongoSessionsArchitecture(unittest.TestCase):
    def test_mongo_opaque_sessions_allow_concurrent_clients(self):
        arch = sessions_architecture_public()
        self.assertEqual(arch["store"], "mongodb")
        self.assertFalse(arch["jwt"])
        self.assertTrue(arch["concurrent_sessions_per_user_allowed"])
        self.assertTrue(arch["shared_across_vms_ha_nodes"])


class CapacitySnapshot(unittest.TestCase):
    def test_18_5_i_capacity_snapshot(self):
        async def _run():
            with patch.object(rwc, "count_users", new=AsyncMock(return_value=3)):
                with patch.object(rwc, "count_active_sessions", new=AsyncMock(return_value=2)):
                    return await get_rdso_18_5_i_capacity(include_counts=True)

        cap = asyncio.run(_run())
        self.assertTrue(cap["rdso_18_5_i"])
        self.assertIsNone(cap["users"]["hard_cap"])
        self.assertIsNone(cap["concurrent_logins"]["hard_cap"])
        self.assertTrue(cap["software_compliant"])
        self.assertEqual(cap["users"]["current_count"], 3)
        self.assertEqual(cap["concurrent_logins"]["current_active_sessions"], 2)


class ConcurrentLogicalSessions(unittest.IsolatedAsyncioTestCase):
    async def test_100_concurrent_logical_session_creates(self):
        """Simulate 100 concurrent create_session inserts (no production HTTP)."""
        inserts: list[dict] = []

        async def fake_insert(doc):
            inserts.append(doc)
            return None

        with patch("app.services.session_service.SESSION_COLLECTION") as coll:
            coll.insert_one = AsyncMock(side_effect=fake_insert)
            from app.services.session_service import create_session

            tokens = await asyncio.gather(
                *(
                    create_session(
                        str(ObjectId()),
                        user={"username": f"u{i}", "role": "Viewer"},
                    )
                    for i in range(100)
                )
            )
        self.assertEqual(len(tokens), 100)
        self.assertEqual(len(set(tokens)), 100)
        self.assertEqual(len(inserts), 100)


if __name__ == "__main__":
    unittest.main()
