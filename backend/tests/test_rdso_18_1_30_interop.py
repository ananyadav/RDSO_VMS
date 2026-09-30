"""RDSO 18.1.30 — ONVIF interoperability + integration package (reuses 18.3.15)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

ROOT = Path(__file__).resolve().parents[2]


class Reuses18315(unittest.TestCase):
    def test_profile_s_resolve_still_works(self):
        from app.services.onvif_stream_uri import parse_stream_uri_response

        xml = """
        <GetStreamUriResponse xmlns:tt="http://www.onvif.org/ver10/schema">
          <MediaUri><tt:Uri>rtsp://10.0.0.1/stream</tt:Uri></MediaUri>
        </GetStreamUriResponse>
        """
        self.assertEqual(parse_stream_uri_response(xml), "rtsp://10.0.0.1/stream")

    def test_profile_g_replay_parse(self):
        from app.services.edge_onvif_recording import parse_replay_uri_response

        xml = "<GetReplayUriResponse><Uri>rtsp://10.0.0.1/replay</Uri></GetReplayUriResponse>"
        self.assertTrue(parse_replay_uri_response(xml).startswith("rtsp://"))

    def test_vendor_fallback_hik_not_forced(self):
        from app.services.onvif_stream_uri import should_resolve_onvif_for_recording

        self.assertFalse(should_resolve_onvif_for_recording({"protocol": "HIKVISION"}))
        self.assertTrue(should_resolve_onvif_for_recording({"protocol": "ONVIF"}))


class SecretMasking(unittest.TestCase):
    def test_mask_rtsp(self):
        from app.services.rtsp_utils import mask_rtsp_url

        masked = mask_rtsp_url("rtsp://admin:secret@192.168.1.1/path")
        self.assertNotIn("secret", masked)
        self.assertNotIn("admin:", masked)


class IntegrationPackage(unittest.TestCase):
    def test_purchaser_package_files(self):
        self.assertTrue((ROOT / "docs" / "integration-api.md").is_file())
        self.assertTrue((ROOT / "docs" / "integration-openapi.json").is_file())
        self.assertTrue((ROOT / "docs" / "integration-package" / "README.md").is_file())
        self.assertTrue((ROOT / "sdk" / "integration" / "vms_client.py").is_file())
        schema = json.loads((ROOT / "docs" / "integration-openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["info"]["version"], "18.1.30")
        self.assertIn("/api/system/interop", schema["paths"])
        self.assertIn("/api/cameras/{id}/client-media", schema["paths"])
        guide = (ROOT / "docs" / "integration-api.md").read_text(encoding="utf-8")
        self.assertIn("18.1.30", guide)
        self.assertIn("client-media", guide)


class ReferenceClient(unittest.TestCase):
    def test_client_imports_and_masks_helper(self):
        sys.path.insert(0, str(ROOT / "sdk" / "integration"))
        from vms_client import VmsClient, assert_no_camera_secrets  # type: ignore

        client = VmsClient("http://127.0.0.1:10000")
        self.assertTrue(hasattr(client, "login"))
        self.assertTrue(hasattr(client, "system_interop"))
        self.assertTrue(hasattr(client, "onvif_profile_s"))
        assert_no_camera_secrets({"password": "***", "main_rtsp_url": "rtsp://192.168.1.1/x"})
        with self.assertRaises(AssertionError):
            assert_no_camera_secrets({"password": "supersecret"})
        with self.assertRaises(AssertionError):
            assert_no_camera_secrets({"url": "rtsp://admin:pass@10.0.0.1/x"})


class SystemInteropStatus(unittest.IsolatedAsyncioTestCase):
    async def test_status_shape_without_mongo_probe_heavy(self):
        from app.services.onvif_interop import get_system_interop_status

        class _Cursor:
            def limit(self, _n):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class _Coll:
            async def count_documents(self, _q):
                return 0

            def find(self, *_a, **_k):
                return _Cursor()

        with patch("app.core.database.camera_collection", _Coll()):
            status = await get_system_interop_status(camera_limit=10)

        self.assertTrue(status["rdso_18_1_30"])
        self.assertTrue(status["rdso_18_3_15"])
        self.assertTrue(status["software_interop_available"])
        self.assertTrue(status["profile_s"]["software_supported"])
        self.assertTrue(status["profile_g"]["software_supported"])
        self.assertTrue(status["integration_api"]["docs_on_disk"])
        self.assertTrue(status["integration_api"]["reference_client_on_disk"])
        self.assertIn(
            "NOT claimed",
            status["tested_capability"]["external_onvif_client_list_certification"],
        )
        self.assertTrue(status["security"]["camera_passwords_never_returned"])

    async def test_route_requires_admin(self):
        from app.routes.onvif_interop import system_interop_endpoint

        req = make_mocked_request("GET", "/api/system/interop")
        with patch(
            "app.routes.onvif_interop.deny_unless_admin",
            new=AsyncMock(
                return_value=web.json_response({"error": "Authentication required"}, status=401)
            ),
        ):
            resp = await system_interop_endpoint(req)
        self.assertEqual(resp.status, 401)


class CameraSummaryFlags(unittest.IsolatedAsyncioTestCase):
    async def test_summary_includes_18_1_30(self):
        from app.services.onvif_interop import get_camera_interop_summary

        cam = {"_id": "x", "protocol": "HIKVISION", "name": "c"}
        with patch(
            "app.services.onvif_interop.detect_edge_storage_capability",
            new=AsyncMock(
                return_value={
                    "supported": False,
                    "protocol": "none",
                    "message": "no edge",
                    "search_supported": False,
                    "storage_present": False,
                }
            ),
        ):
            summary = await get_camera_interop_summary(cam, resolve_streams=False)
        self.assertTrue(summary["rdso_18_1_30"])
        self.assertTrue(summary["rdso_18_3_15"])
        self.assertFalse(summary["profile_s"]["supported"])  # honest vendor template message
        self.assertIn("system_interop", summary["integration_api"])


if __name__ == "__main__":
    unittest.main()
