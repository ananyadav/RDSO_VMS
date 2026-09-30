"""RDSO 18.6.17.3 — third-party VMS integration framework (mock adapters in tests only)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ccc_external_vms_rest_adapter import GenericRestVmsSource
from app.services.ccc_vms_adapter_errors import (
    UnsupportedCapabilityError,
    VmsAuthFailureError,
    VmsDirectCameraForbiddenError,
    VmsSourceOfflineError,
    VmsTimeoutError,
)
from app.services.ccc_vms_alert_ingest import ingest_vms_alert
from app.services.ccc_vms_credential_crypto import decrypt_credential, encrypt_credential
from app.services.ccc_vms_integration_store import (
    create_integration,
    integration_to_public,
    list_integrations,
    rotate_ingest_secret,
    update_integration,
    verify_ingest_secret,
)
from app.services.ccc_vms_source import (
    LocalVmsSource,
    count_external_vms_sources,
    get_ccc_source,
    list_ccc_sources,
    register_ccc_source_for_tests,
    reload_external_vms_sources,
    sanitize_ccc_media_payload,
    unregister_ccc_source_for_tests,
    ccc_capability_public,
)


class MockExternalVmsSource:
    """Test-only external adapter — not registered in production."""

    source_id = "mock-ext"
    label = "Mock External VMS"

    def describe(self):
        return {
            "source_id": self.source_id,
            "label": self.label,
            "kind": "external_vms",
            "external_vendor_integration": True,
            "named_vendor_sdk": False,
            "capabilities": {"live": True, "playback": True, "events": True, "health": True},
        }

    async def list_cameras_public(self, user, *, limit=100, offset=0, q=""):
        return {
            "source_id": self.source_id,
            "items": [
                {
                    "id": f"{self.source_id}:cam1",
                    "name": "Ext Cam",
                    "camera_uid": "ext_cam1",
                    "online": True,
                    "password": None,
                    "main_rtsp_url": None,
                }
            ],
            "total": 1,
            "limit": limit,
            "offset": offset,
            "returned": 1,
            "streams_not_auto_started": True,
        }

    async def client_media_for(self, camera_ref, user):
        return sanitize_ccc_media_payload(
            {
                "live": {"worker_id": 1, "ws_path": "/proxy/ext/ws", "relative_path": True},
                "password": "secret",
            }
        )

    async def health_status(self):
        return {"source_id": self.source_id, "status": "healthy"}

    async def search_playback_public(self, user, camera_ref, *, from_ts="", to_ts=""):
        return {
            "source_id": self.source_id,
            "camera_ref": camera_ref,
            "supported": True,
            "items": [{"session_id": "s1"}],
        }


class MockUnsupportedSource(MockExternalVmsSource):
    source_id = "mock-unsupported"
    label = "Mock Unsupported"

    def describe(self):
        d = super().describe()
        d["source_id"] = self.source_id
        d["capabilities"] = {"live": False, "playback": False, "events": True, "health": False}
        return d

    async def list_cameras_public(self, user, *, limit=100, offset=0, q=""):
        raise UnsupportedCapabilityError("live not supported")


def _admin():
    return {"id": "a1", "name": "Admin", "role": "Admin", "permissions": ["Events", "Live View"]}


class CapabilityClause(unittest.TestCase):
    def test_17_3_framework_flag(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.17.3"])
        self.assertFalse(cap["vms_integration"]["named_vendor_sdks_bundled"])
        self.assertFalse(cap["vms_integration"]["fake_vendors"])


class CredentialCrypto(unittest.TestCase):
    def setUp(self):
        os.environ["CCC_VMS_CREDENTIAL_KEY"] = "test-key-for-17-3"

    def test_encrypt_decrypt_roundtrip(self):
        enc = encrypt_credential("api-secret-123")
        self.assertNotIn("api-secret", enc)
        self.assertEqual(decrypt_credential(enc), "api-secret-123")

    def test_public_never_has_credential(self):
        pub = integration_to_public(
            {
                "source_id": "ext1",
                "label": "X",
                "vendor_type": "generic_rest",
                "base_url": "https://vms.example.com",
                "credential_encrypted": encrypt_credential("sek"),
                "ingest_secret_hash": "x",
            }
        )
        self.assertTrue(pub["has_credentials"])
        self.assertNotIn("credential", pub)
        self.assertNotIn("credential_encrypted", pub)


class RegistryAndLocalRegression(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        unregister_ccc_source_for_tests("mock-ext")
        unregister_ccc_source_for_tests("mock-unsupported")

    async def asyncTearDown(self):
        unregister_ccc_source_for_tests("mock-ext")
        unregister_ccc_source_for_tests("mock-unsupported")

    async def test_local_source_unchanged(self):
        src = get_ccc_source("local")
        self.assertIsInstance(src, LocalVmsSource)
        desc = src.describe()
        self.assertEqual(desc["source_id"], "local")
        self.assertFalse(desc["external_vendor_integration"])
        health = await src.health_status()
        self.assertIn(health["status"], ("healthy", "degraded"))

    async def test_multiple_sources_via_registry(self):
        register_ccc_source_for_tests(MockExternalVmsSource())
        sources = list_ccc_sources()
        ids = {s["source_id"] for s in sources}
        self.assertIn("local", ids)
        self.assertIn("mock-ext", ids)
        self.assertEqual(count_external_vms_sources(), 1)

    async def test_live_through_mock_adapter(self):
        register_ccc_source_for_tests(MockExternalVmsSource())
        src = get_ccc_source("mock-ext")
        cams = await src.list_cameras_public(_admin(), limit=10)
        self.assertEqual(cams["items"][0]["id"], "mock-ext:cam1")
        media = await src.client_media_for("mock-ext:cam1", _admin())
        self.assertTrue(media["ccc_no_direct_camera"])
        self.assertNotIn("password", media)

    async def test_playback_through_mock_adapter(self):
        register_ccc_source_for_tests(MockExternalVmsSource())
        src = get_ccc_source("mock-ext")
        pb = await src.search_playback_public(_admin(), "mock-ext:cam1")
        self.assertTrue(pb["supported"])
        self.assertEqual(pb["items"][0]["session_id"], "s1")

    async def test_unsupported_capability_honest(self):
        register_ccc_source_for_tests(MockUnsupportedSource())
        src = get_ccc_source("mock-unsupported")
        with self.assertRaises(UnsupportedCapabilityError):
            await src.list_cameras_public(_admin())


class IntegrationStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        os.environ["CCC_VMS_CREDENTIAL_KEY"] = "test-key-for-17-3"
        self._docs: list[dict] = []

        async def insert_one(doc):
            doc = dict(doc)
            self._docs.append(doc)

        async def find_one(query):
            if "source_id" in query:
                for d in self._docs:
                    if d.get("source_id") == query["source_id"]:
                        return d
            return None

        async def update_one(query, ops, upsert=False):
            doc = await find_one(query)
            if doc and "$set" in ops:
                doc.update(ops["$set"])
            elif upsert and "$set" in ops:
                self._docs.append(dict(ops["$set"]))

        async def delete_one(query):
            sid = query.get("source_id")
            self._docs = [d for d in self._docs if d.get("source_id") != sid]

        class Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    for d in self._items:
                        yield d

                return gen()

        self._coll = MagicMock()
        self._coll.insert_one = AsyncMock(side_effect=insert_one)
        self._coll.find_one = AsyncMock(side_effect=find_one)
        self._coll.update_one = AsyncMock(side_effect=update_one)
        self._coll.delete_one = AsyncMock(side_effect=delete_one)
        self._coll.find = MagicMock(
            side_effect=lambda q: Cursor(
                [d for d in self._docs if not q.get("enabled") or d.get("enabled")]
            )
        )
        self._p = patch(
            "app.services.ccc_vms_integration_store.integrations_collection", self._coll
        )
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_create_enable_disable(self):
        pub, secret = await create_integration(
            {
                "source_id": "ext-test",
                "label": "Purchaser REST",
                "base_url": "https://vms.example.com",
                "credential": "tok",
                "enabled": True,
                "capabilities": {"live": True, "events": True},
            }
        )
        self.assertEqual(pub["source_id"], "ext-test")
        self.assertTrue(pub["has_credentials"])
        self.assertFalse(pub["fake_vendor"])
        self.assertIsNotNone(secret)
        pub2 = await update_integration("ext-test", {"enabled": False})
        self.assertFalse(pub2["enabled"])

    async def test_rotate_ingest_secret(self):
        await create_integration(
            {
                "source_id": "ext2",
                "label": "X",
                "base_url": "https://vms.example.com",
                "credential": "k",
            }
        )
        pub, secret = await rotate_ingest_secret("ext2")
        self.assertTrue(pub["has_ingest_secret"])
        self.assertTrue(await verify_ingest_secret("ext2", secret))
        self.assertFalse(await verify_ingest_secret("ext2", "wrong"))


class GenericRestAdapterErrors(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure(self):
        src = GenericRestVmsSource(
            {
                "source_id": "ext-auth",
                "label": "X",
                "base_url": "https://vms.example.com",
                "capabilities": {"live": True, "health": True},
                "endpoints": {"health": "/health"},
                "credential": "bad",
                "tls_verify": True,
                "timeout_seconds": 5,
            }
        )

        class Resp:
            status = 401

            async def text(self):
                return "unauthorized"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class Session:
            def request(self, *a, **k):
                return Resp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        with patch("aiohttp.ClientSession", return_value=Session()), patch(
            "aiohttp.TCPConnector", return_value=MagicMock()
        ), patch(
            "app.services.ccc_external_vms_rest_adapter.touch_integration_health",
            new=AsyncMock(),
        ):
            result = await src.health_status()
        self.assertEqual(result["status"], "offline")
        self.assertEqual(result.get("code"), "authentication_failure")

    async def test_rtsp_forbidden(self):
        src = GenericRestVmsSource(
            {
                "source_id": "ext-rtsp",
                "label": "X",
                "base_url": "https://vms.example.com",
                "capabilities": {"live": True},
                "endpoints": {"live_media": "/media/{camera_id}"},
                "credential": "tok",
            }
        )
        with patch.object(
            src,
            "_request",
            new=AsyncMock(return_value={"live": {"rtsp_url": "rtsp://cam/live"}}),
        ):
            with self.assertRaises(VmsDirectCameraForbiddenError):
                await src.client_media_for("ext-rtsp:1", _admin())


class AlertNormalization(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_creates_event_via_pipeline(self):
        with patch(
            "app.services.ccc_vms_alert_ingest.create_event",
            new=AsyncMock(return_value={"id": "evt-1", "title": "A"}),
        ) as create:
            out = await ingest_vms_alert(
                source_id="ext1",
                body={
                    "title": "Door forced",
                    "severity": "critical",
                    "source_type": "external_sensor",
                    "metadata": {"zone": "Gate-A"},
                },
            )
        self.assertTrue(out["via_existing_pipeline"])
        self.assertFalse(out["second_alarm_engine"])
        create.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
