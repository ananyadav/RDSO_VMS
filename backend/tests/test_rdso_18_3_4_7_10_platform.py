"""RDSO 18.3.4 / 18.3.7 / 18.3.10 — platform, network access, recording capacity status."""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.recording_platform import (
    get_network_access_evidence,
    get_open_architecture_evidence,
    get_recording_platform_status,
)
from app.services.storage_volume import (
    STATUS_CRITICAL,
    STATUS_LOW_SPACE,
    STATUS_ONLINE,
    STATUS_UNAVAILABLE,
    disk_payload_from_probe,
    probe_storage_path,
)


class OpenArchitecture1834(unittest.TestCase):
    def test_open_architecture_capability_status(self):
        evidence = get_open_architecture_evidence()
        self.assertTrue(evidence["rdso_18_3_4"])
        self.assertTrue(evidence["open_architecture"])
        self.assertFalse(evidence["proprietary_hardware_required"])
        names = {d["name"] for d in evidence["dependencies"]}
        self.assertIn("FFmpeg", names)
        self.assertIn("MongoDB", names)
        self.assertIn("OS filesystem", names)
        self.assertIn("RTSP / ONVIF", names)
        self.assertIn("Windows", evidence["platform"]["supported_os"])
        self.assertIn("Linux", evidence["platform"]["supported_os"])

    def test_platform_bundle(self):
        bundle = get_recording_platform_status()
        self.assertIn("open_architecture", bundle)
        self.assertIn("network_access", bundle)


class NetworkAccess1837(unittest.IsolatedAsyncioTestCase):
    def test_network_evidence_not_localhost_only_logic(self):
        evidence = get_network_access_evidence()
        self.assertTrue(evidence["rdso_18_3_7"])
        self.assertTrue(evidence["network_accessible"])
        self.assertFalse(evidence["localhost_only_application_logic"])
        self.assertTrue(evidence["auth_required"])
        self.assertTrue(evidence["rbac_enforced"])

    def test_recording_routes_have_no_localhost_caller_guard(self):
        from app.routes import recording as recording_routes

        src = Path(inspect.getfile(recording_routes)).read_text(encoding="utf-8")
        # Application must not refuse non-localhost remotes in route handlers
        self.assertNotIn('remote == "127.0.0.1"', src)
        self.assertNotIn("request.remote == '127.0.0.1'", src)

    async def test_capability_requires_auth(self):
        from app.routes.recording import recording_capability_endpoint

        req = make_mocked_request("GET", "/api/recordings/capability")
        with patch(
            "app.routes.recording.require_user",
            new=AsyncMock(side_effect=web.HTTPUnauthorized()),
        ):
            resp = await recording_capability_endpoint(req)
        self.assertEqual(resp.status, 401)

    async def test_storage_dashboard_requires_super_admin(self):
        from app.routes.recording import storage_dashboard_endpoint

        req = make_mocked_request("GET", "/api/storage/dashboard")
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new=AsyncMock(
                return_value=web.json_response({"error": "Forbidden"}, status=403)
            ),
        ):
            resp = await storage_dashboard_endpoint(req)
        self.assertEqual(resp.status, 403)


class CapacityStatus18310(unittest.TestCase):
    def test_capacity_values_and_online_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(500 * 1024**3, 100 * 1024**3, 400 * 1024**3, None),
            ), patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "1",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_ONLINE)
            disk = disk_payload_from_probe(probe)
            self.assertEqual(disk["total_gb"], disk["disk_total_gb"])
            self.assertEqual(disk["used_gb"], disk["disk_used_gb"])
            self.assertEqual(disk["free_gb"], disk["disk_free_gb"])
            self.assertEqual(disk["percent_free"], disk["disk_free_percent"])
            self.assertEqual(disk["percent_used"], disk["disk_percent"])
            self.assertTrue(disk["rdso_18_3_10"])
            self.assertEqual(disk["status_label"], "Online")

    def test_low_space_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(1000 * 1024**3, 880 * 1024**3, 120 * 1024**3, None),
            ), patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "1",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_LOW_SPACE)
            self.assertEqual(probe["status_label"], "Low Space")
            self.assertTrue(probe["allow_recording"])
            self.assertEqual(probe["status_level"], "yellow")

    def test_critical_blocks_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(1000 * 1024**3, 970 * 1024**3, 30 * 1024**3, None),
            ), patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "1",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_CRITICAL)
            self.assertEqual(probe["status_label"], "Critical")
            self.assertFalse(probe["allow_recording"])
            self.assertEqual(probe["status_level"], "red")

    def test_unavailable_no_fallback(self):
        missing = Path(tempfile.gettempdir()) / f"vms_18_3_10_missing_{os.getpid()}"
        probe = probe_storage_path(missing, create_if_missing=False)
        self.assertEqual(probe["status"], STATUS_UNAVAILABLE)
        self.assertEqual(probe["status_label"], "Unavailable")
        self.assertIsNone(probe.get("total_bytes"))


class CapabilityPayload(unittest.IsolatedAsyncioTestCase):
    async def test_capability_includes_platform_and_capacity(self):
        from app.routes.recording import recording_capability_endpoint

        req = make_mocked_request("GET", "/api/recordings/capability")
        with patch("app.routes.recording.require_user", new=AsyncMock(return_value={"id": "u1"})):
            with patch(
                "app.services.storage_volume.probe_recordings_storage",
                return_value={
                    "path": "/data",
                    "disk_total_gb": 100.0,
                    "disk_used_gb": 40.0,
                    "disk_free_gb": 60.0,
                    "disk_free_percent": 60.0,
                    "disk_percent": 40.0,
                    "status": STATUS_ONLINE,
                    "status_label": "Online",
                    "status_level": "green",
                    "writable": True,
                    "allow_recording": True,
                    "storage_model": "os_filesystem",
                },
            ):
                resp = await recording_capability_endpoint(req)
        self.assertEqual(resp.status, 200)
        # aiohttp json response body
        import json

        body = json.loads(resp.text)
        self.assertTrue(body["rdso"]["18.3.4_open_architecture"])
        self.assertTrue(body["rdso"]["18.3.7_network_access"])
        self.assertTrue(body["rdso"]["18.3.10_capacity_status"])
        self.assertFalse(body["platform"]["open_architecture"]["proprietary_hardware_required"])
        self.assertEqual(body["recording_capacity_status"]["status_label"], "Online")


if __name__ == "__main__":
    unittest.main()
