"""RDSO 18.6.22.8 — open CCC third-party API / SDK integration facade."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_device_ingest import CccIngestError, authenticate_device_ingest, ingest_device_alert
from app.services.ccc_device_service import device_to_public, hash_integration_secret
from app.services.ccc_open_integration import (
    extract_location_metadata,
    open_integration_contract,
    open_integration_identity,
)
from app.services.ccc_vms_alert_ingest import VmsAlertIngestError, ingest_vms_alert
from app.services.ccc_vms_source import ccc_capability_public


def _device_doc(*, enabled=True, secret="sekrit"):
    return {
        "_id": ObjectId(),
        "device_uid": "ext_sensor_1",
        "type": "external_sensor",
        "name": "Gate sensor",
        "location": "Gate-A",
        "enabled": enabled,
        "api_key_hash": hash_integration_secret(secret),
        "linked_camera_ids": [],
        "default_priority": 3,
        "active_alert_count": 0,
        "metadata": {},
        "ingest_window_started": None,
        "ingest_window_count": 0,
    }


class CapabilityClause(unittest.TestCase):
    def test_18_6_22_8_flag_and_open_block(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.22.8"])
        oi = cap["open_integration"]
        self.assertTrue(oi["rdso_18_6_22_8"])
        self.assertFalse(oi["second_alarm_engine"])
        self.assertFalse(oi["named_vendor_sdks_bundled"])
        self.assertFalse(oi["gis"])
        self.assertFalse(oi["gis_format_storage"])
        self.assertTrue(oi["idempotency"])
        self.assertTrue(oi["location_metadata_preserved"])
        self.assertEqual(oi["events_path"], "/api/ccc/integrations/open/events")
        # GIS still deferred at platform level
        self.assertFalse(cap["clauses"]["18.6.14_gis"])

    def test_contract_and_identity_no_secrets(self):
        contract = open_integration_contract()
        identity = open_integration_identity()
        self.assertTrue(contract["rdso_18_6_22_8"])
        self.assertFalse(contract["gis_format_storage"])
        blob = str(contract) + str(identity)
        self.assertNotIn("password", blob.lower().replace("gis_format_storage", ""))
        self.assertNotIn("credential_encrypted", blob)
        self.assertNotIn("api_key_hash", blob)


class LocationMetadata(unittest.TestCase):
    def test_preserves_coords_without_claiming_gis(self):
        loc = extract_location_metadata(
            {"location": "Platform-1", "latitude": 28.61, "longitude": 77.20, "altitude": 210}
        )
        self.assertEqual(loc["location"], "Platform-1")
        self.assertEqual(loc["geo"]["lat"], 28.61)
        self.assertEqual(loc["geo"]["lon"], 77.20)
        self.assertEqual(loc["geo"]["alt"], 210.0)
        self.assertFalse(loc["gis_format_stored"])
        self.assertTrue(loc["gis_deferred"])


class DeviceIngestPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_event_ingestion_existing_pipeline_and_location(self):
        doc = _device_doc()
        created = []

        async def _create(**kwargs):
            created.append(kwargs)
            return {"id": "evt-1", "title": kwargs["title"], "metadata": kwargs.get("metadata")}

        with patch(
            "app.services.ccc_device_ingest.create_event", new=AsyncMock(side_effect=_create)
        ), patch(
            "app.services.ccc_device_ingest.devices_collection.update_one", new=AsyncMock()
        ), patch(
            "app.services.ccc_device_ingest.lookup_idempotent_result", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.ccc_device_ingest.store_idempotent_result", new=AsyncMock()
        ):
            out = await ingest_device_alert(
                doc,
                body={
                    "title": "Door forced",
                    "severity": "critical",
                    "source_type": "external_sensor",
                    "location": "Gate-A",
                    "latitude": 28.6,
                    "longitude": 77.2,
                    "timestamp": "2026-09-14T10:00:00Z",
                    "external_event_id": "ext-1",
                },
            )
        self.assertTrue(out["event_created"])
        self.assertEqual(out["pipeline"], "existing_event_service")
        self.assertFalse(out["second_alarm_engine"])
        self.assertEqual(created[0]["metadata"]["location"], "Gate-A")
        self.assertEqual(created[0]["metadata"]["geo"]["lat"], 28.6)
        self.assertFalse(created[0]["metadata"]["gis_format_stored"])
        self.assertIsNotNone(created[0]["occurred_at"])

    async def test_idempotency_duplicate(self):
        doc = _device_doc()
        prior = {
            "ok": True,
            "event_id": "evt-prior",
            "pipeline": "existing_event_service",
            "second_alarm_engine": False,
        }
        with patch(
            "app.services.ccc_device_ingest.lookup_idempotent_result",
            new=AsyncMock(return_value=prior),
        ), patch(
            "app.services.ccc_device_ingest.create_event", new=AsyncMock()
        ) as create:
            out = await ingest_device_alert(
                doc, body={"title": "X", "external_event_id": "same-key"}
            )
        create.assert_not_awaited()
        self.assertTrue(out["duplicate"])
        self.assertEqual(out["event_id"], "evt-prior")
        self.assertFalse(out["event_created"])

    async def test_oversized_payload(self):
        doc = _device_doc()
        body = {"title": "x", "message": "y" * 20000}
        with self.assertRaises(CccIngestError) as ctx:
            await ingest_device_alert(doc, body=body)
        self.assertIn("exceeds", str(ctx.exception).lower())

    async def test_disabled_device(self):
        doc = _device_doc(enabled=False)
        with self.assertRaises(CccIngestError):
            await authenticate_device_ingest(doc, "sekrit")

    async def test_auth_failure(self):
        doc = _device_doc(secret="good")
        with self.assertRaises(CccIngestError):
            await authenticate_device_ingest(doc, "bad")

    async def test_secret_redaction_in_public_device(self):
        doc = _device_doc()
        pub = device_to_public(doc)
        self.assertNotIn("api_key_hash", pub)
        self.assertNotIn("password", pub)
        self.assertNotIn("integration_secret", pub)


class VmsIngestParity(unittest.IsolatedAsyncioTestCase):
    async def test_vms_event_existing_pipeline(self):
        with patch(
            "app.services.ccc_vms_alert_ingest.create_event",
            new=AsyncMock(return_value={"id": "e2"}),
        ) as create, patch(
            "app.services.ccc_vms_alert_ingest.lookup_idempotent_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.services.ccc_vms_alert_ingest.store_idempotent_result", new=AsyncMock()
        ):
            out = await ingest_vms_alert(
                source_id="ext-vms",
                body={
                    "title": "Zone alarm",
                    "source_type": "external_sensor",
                    "location": "Yard",
                    "lat": 1.2,
                    "lon": 3.4,
                    "external_event_id": "v-1",
                },
            )
        self.assertTrue(out["via_existing_pipeline"])
        self.assertFalse(out["second_alarm_engine"])
        md = create.await_args.kwargs["metadata"]
        self.assertEqual(md["vms_source_id"], "ext-vms")
        self.assertEqual(md["geo"]["lat"], 1.2)
        self.assertFalse(md["gis_format_stored"])

    async def test_vms_oversized(self):
        with self.assertRaises(VmsAlertIngestError):
            await ingest_vms_alert(
                source_id="ext-vms",
                body={"title": "x", "message": "z" * 20000},
            )


class OpenFacadeRoutesSmoke(unittest.TestCase):
    def test_setup_registers_open_paths(self):
        from aiohttp import web

        from app.routes.ccc import setup_ccc_routes

        app = web.Application()
        setup_ccc_routes(app)
        paths = {r.resource.canonical for r in app.router.routes() if hasattr(r, "resource") and r.resource}
        self.assertIn("/api/ccc/integrations/open/capability", paths)
        self.assertIn("/api/ccc/integrations/open/events", paths)
        self.assertIn("/api/ccc/integrations/open/register", paths)
        self.assertIn("/api/ccc/devices/{id}/ingest", paths)
        self.assertIn("/api/ccc/vms-integrations/{sourceId}/alerts", paths)


class ContractHonesty(unittest.TestCase):
    def test_no_named_vendor_claim(self):
        c = open_integration_contract()
        self.assertFalse(c["named_vendor_sdks_bundled"])
        self.assertFalse(c["fake_vendors"])
        self.assertIn("GenericRestVmsSource", str(c["reuses"]["vms_adapters"]))


if __name__ == "__main__":
    unittest.main()
