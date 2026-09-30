"""RDSO 18.6.22.3 / 18.6.22.12 / 18.6.22.15 — CCC devices, ingest, admin, pre-emption."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_device_ingest import CccIngestError, ingest_device_alert
from app.services.ccc_device_service import (
    create_device,
    delete_device,
    device_to_public,
    hash_integration_secret,
    list_devices,
    update_device,
    verify_integration_secret,
)
from app.services.ccc_preemption import (
    evaluate_preemption,
    get_preemption_policy,
    save_preemption_policy,
)
from app.services.ccc_vms_source import ccc_capability_public


def _admin():
    return {"id": "a1", "name": "Admin", "role": "Admin", "permissions": ["Events"]}


def _async_cursor(docs):
    class Cursor:
        def sort(self, *_a, **_k):
            return self

        def skip(self, n=0):
            self._docs = self._docs[n:]
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def __init__(self, items=None):
            self._docs = list(items if items is not None else docs)

        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d

            return gen()

    return Cursor()


class Capability(unittest.TestCase):
    def test_clauses(self):
        cap = ccc_capability_public()
        for c in ("18.6.22.3", "18.6.22.12", "18.6.22.15"):
            self.assertTrue(cap["clauses"][c], c)
        self.assertFalse(cap["devices"]["fake_vendors"])
        self.assertFalse(cap["devices"]["second_alarm_engine"])
        self.assertFalse(cap["devices"]["hard_count_cap"])
        self.assertFalse(cap["devices"]["opens_streams_on_register"])


class DeviceCrud(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._docs: list[dict] = []

        async def insert_one(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            self._docs.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        async def find_one(query):
            if "_id" in query:
                for d in self._docs:
                    if d["_id"] == query["_id"]:
                        return d
            if "device_uid" in query:
                for d in self._docs:
                    if d.get("device_uid") == query["device_uid"]:
                        return d
            return None

        async def update_one(query, ops):
            doc = await find_one(query)
            if doc and "$set" in ops:
                doc.update(ops["$set"])

        async def delete_one(query):
            before = len(self._docs)
            self._docs[:] = [d for d in self._docs if d["_id"] != query.get("_id")]

            class R:
                deleted_count = before - len(self._docs)

            return R()

        async def count_documents(query):
            return len(self._docs)

        def find(query):
            return _async_cursor(self._docs)

        mock = MagicMock()
        mock.insert_one = AsyncMock(side_effect=insert_one)
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)
        mock.delete_one = AsyncMock(side_effect=delete_one)
        mock.count_documents = AsyncMock(side_effect=count_documents)
        mock.find = MagicMock(side_effect=find)
        self._p = patch("app.services.ccc_device_service.devices_collection", mock)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_add_update_disable_remove(self):
        d = await create_device(name="Fence PIR", type="external_sensor", location="Gate")
        self.assertEqual(d["type"], "external_sensor")
        self.assertTrue(d["enabled"])
        self.assertTrue(d["has_integration_secret"])
        self.assertIsNotNone(d.get("integration_secret"))
        self.assertFalse(d["opens_streams_on_register"])
        self.assertNotIn("api_key_hash", d)

        upd = await update_device(d["id"], {"enabled": False, "location": "Gate 2"})
        self.assertFalse(upd["enabled"])
        self.assertEqual(upd["location"], "Gate 2")
        self.assertEqual(upd["status"], "disabled")

        listed = await list_devices()
        self.assertEqual(listed["total"], 1)
        self.assertFalse(listed["hard_count_cap"])

        ok = await delete_device(d["id"])
        self.assertTrue(ok)
        listed2 = await list_devices()
        self.assertEqual(listed2["total"], 0)

    async def test_secret_hash_roundtrip_and_redaction(self):
        raw = "super-secret-token"
        hashed = hash_integration_secret(raw)
        self.assertTrue(verify_integration_secret(raw, hashed))
        self.assertFalse(verify_integration_secret("wrong", hashed))
        pub = device_to_public(
            {
                "_id": ObjectId(),
                "name": "x",
                "type": "external_sensor",
                "api_key_hash": hashed,
                "metadata": {"password": "nope", "token": "t"},
            }
        )
        self.assertNotIn("api_key_hash", pub)
        meta = pub.get("metadata") or {}
        self.assertEqual(meta.get("password"), "[REDACTED]")
        self.assertEqual(meta.get("token"), "[REDACTED]")

    async def test_rejects_fake_vendor_type(self):
        from app.services.ccc_device_service import CccDeviceError

        with self.assertRaises(CccDeviceError):
            await create_device(name="X", type="hikvision_magic")


class IngestPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_valid_alert_creates_event(self):
        doc = {
            "_id": ObjectId(),
            "device_uid": "pir-1",
            "type": "external_sensor",
            "name": "PIR",
            "location": "Gate",
            "enabled": True,
            "default_priority": 4,
            "linked_camera_ids": [],
            "active_alert_count": 0,
            "metadata": {},
            "api_key_hash": hash_integration_secret("sek"),
        }
        created = {
            "id": str(ObjectId()),
            "source_type": "external_sensor",
            "title": "Intrusion",
            "priority": 4,
            "severity": "critical",
        }
        with patch(
            "app.services.ccc_device_ingest.create_event",
            new=AsyncMock(return_value=created),
        ) as ce, patch(
            "app.services.ccc_device_ingest.devices_collection.update_one",
            new=AsyncMock(),
        ):
            result = await ingest_device_alert(
                doc,
                body={
                    "kind": "alert",
                    "title": "Intrusion",
                    "severity": "critical",
                    "priority": 5,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "location": "Gate",
                    "metadata": {"zone": "A"},
                },
            )
        self.assertTrue(result["event_created"])
        self.assertFalse(result["second_alarm_engine"])
        self.assertEqual(result["pipeline"], "existing_event_service")
        ce.assert_awaited()
        kwargs = ce.await_args.kwargs
        self.assertEqual(kwargs["source_type"], "external_sensor")
        self.assertEqual(kwargs["camera_id"], "")
        self.assertEqual(kwargs["metadata"]["ccc_device_uid"], "pir-1")

    async def test_disabled_device_rejected_at_auth(self):
        from app.services.ccc_device_ingest import authenticate_device_ingest

        doc = {
            "_id": ObjectId(),
            "enabled": False,
            "api_key_hash": hash_integration_secret("sek"),
        }
        with self.assertRaises(CccIngestError):
            await authenticate_device_ingest(doc, "sek")

    async def test_invalid_secret_rejected(self):
        from app.services.ccc_device_ingest import authenticate_device_ingest

        doc = {
            "_id": ObjectId(),
            "enabled": True,
            "api_key_hash": hash_integration_secret("sek"),
        }
        with self.assertRaises(CccIngestError):
            await authenticate_device_ingest(doc, "wrong")

    async def test_heartbeat_no_event(self):
        doc = {
            "_id": ObjectId(),
            "device_uid": "pir-1",
            "type": "external_sensor",
            "enabled": True,
            "metadata": {},
            "status": "unknown",
        }
        with patch(
            "app.services.ccc_device_ingest.touch_device_heartbeat",
            new=AsyncMock(return_value={"id": "x", "status": "online"}),
        ), patch(
            "app.services.ccc_device_ingest.devices_collection.update_one",
            new=AsyncMock(),
        ), patch(
            "app.services.ccc_device_ingest.create_event",
            new=AsyncMock(),
        ) as ce:
            result = await ingest_device_alert(doc, body={"kind": "heartbeat", "status": "online"})
        self.assertFalse(result["event_created"])
        ce.assert_not_awaited()


class LargeRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_no_hard_cap(self):
        docs = [
            {
                "_id": ObjectId(),
                "device_uid": f"d{i}",
                "name": f"D{i}",
                "type": "external_sensor",
                "enabled": True,
                "status": "online",
                "health": "ok",
                "capabilities": [],
                "linked_camera_ids": [],
                "metadata": {},
            }
            for i in range(5)
        ]
        mock = MagicMock()
        mock.count_documents = AsyncMock(return_value=50_000)
        mock.find = MagicMock(return_value=_async_cursor(docs))
        with patch("app.services.ccc_device_service.devices_collection", mock):
            data = await list_devices(limit=5, offset=0)
        self.assertEqual(data["total"], 50_000)
        self.assertFalse(data["hard_count_cap"])
        self.assertEqual(len(data["items"]), 5)


class Preemption(unittest.IsolatedAsyncioTestCase):
    async def test_priority_rules(self):
        pol = {
            "enabled": True,
            "equal_priority": "incoming_wins",
            "min_priority_to_preempt": 1,
            "scopes": {"ccc_control": True, "live_display": True, "ptz_shared": False},
        }
        high = evaluate_preemption(actor_priority=5, holder_priority=2, policy=pol)
        self.assertTrue(high["preempt"])
        low = evaluate_preemption(actor_priority=2, holder_priority=5, policy=pol)
        self.assertFalse(low["preempt"])
        eq = evaluate_preemption(actor_priority=3, holder_priority=3, policy=pol)
        self.assertTrue(eq["preempt"])
        self.assertEqual(eq["reason"], "equal_incoming_wins")

        pol2 = {**pol, "equal_priority": "holder_wins"}
        eq2 = evaluate_preemption(actor_priority=3, holder_priority=3, policy=pol2)
        self.assertFalse(eq2["preempt"])

    async def test_policy_persist(self):
        store: dict = {}

        async def find_one(query):
            return store.get(query.get("_id"))

        async def update_one(query, ops, upsert=False):
            doc = store.get(query["_id"]) or {"_id": query["_id"]}
            if "$set" in ops:
                doc.update(ops["$set"])
            store[query["_id"]] = doc

        mock = MagicMock()
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)
        with patch("app.services.ccc_preemption._settings", mock):
            saved = await save_preemption_policy(
                {"equal_priority": "holder_wins", "min_priority_to_preempt": 3}
            )
            self.assertEqual(saved["equal_priority"], "holder_wins")
            self.assertEqual(saved["min_priority_to_preempt"], 3)
            got = await get_preemption_policy()
            self.assertEqual(got["equal_priority"], "holder_wins")


class RouteRbac(unittest.IsolatedAsyncioTestCase):
    async def test_create_requires_admin(self):
        from aiohttp.test_utils import make_mocked_request

        from app.routes.ccc_devices import ccc_devices_create

        req = make_mocked_request("POST", "/api/ccc/devices")
        req["auth_user"] = {
            "id": "o1",
            "role": "Operator",
            "permissions": ["Events"],
        }
        resp = await ccc_devices_create(req)
        self.assertEqual(resp.status, 403)


if __name__ == "__main__":
    unittest.main()
