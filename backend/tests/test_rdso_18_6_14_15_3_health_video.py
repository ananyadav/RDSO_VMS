"""RDSO 18.6.15.3 Video Management + 18.6.14 Health Reports (non-GIS)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_health_report_service import (
    HEALTH_PAGE_MAX,
    build_ccc_health_report,
    export_health_csv,
)
from app.services.ccc_vms_source import (
    LocalVmsSource,
    ccc_capability_public,
    ccc_public_camera,
    sanitize_ccc_media_payload,
)


def _admin():
    return {
        "id": "a1",
        "name": "Admin",
        "role": "Admin",
        "permissions": ["Events", "Live View"],
    }


def _async_cursor(docs):
    class Cursor:
        def __init__(self, items=None):
            self._docs = list(items if items is not None else docs)

        def sort(self, *_a, **_k):
            return self

        def skip(self, n):
            self._docs = self._docs[n:]
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d

            return gen()

    return Cursor()


class CapabilityClauses(unittest.TestCase):
    def test_15_3_and_14_flags(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.15.3"])
        self.assertTrue(cap["clauses"]["18.6.14"])
        self.assertFalse(cap["clauses"]["18.6.14_gis"])
        self.assertTrue(cap["video_management"]["rdso_18_6_15_3"])
        self.assertEqual(cap["video_management"]["max_live_tiles"], 16)
        self.assertTrue(cap["video_management"]["direct_camera_rtsp_forbidden"])
        self.assertTrue(cap["health_reports"]["rdso_18_6_14_health"])
        self.assertFalse(cap["health_reports"]["rdso_18_6_14_gis"])
        self.assertFalse(cap["health_reports"]["gis"])
        self.assertFalse(cap["health_reports"]["fabricated_metrics"])


class VideoManagementCameras(unittest.IsolatedAsyncioTestCase):
    async def test_camera_list_paginated_no_credentials(self):
        cams = []
        for i in range(5):
            cams.append(
                {
                    "_id": ObjectId(),
                    "name": f"Cam{i}",
                    "display_name": f"Cam {i}",
                    "camera_uid": f"uid{i}",
                    "online": i % 2 == 0,
                    "location_path": f"Site / Floor / Area{i}",
                    "password": "SECRET",
                    "main_rtsp_url": "rtsp://cam/secret",
                    "is_active": True,
                    "worker_id": 1,
                }
            )
        col = MagicMock()
        col.count_documents = AsyncMock(return_value=5)
        col.find = MagicMock(return_value=_async_cursor(cams))

        with patch("app.core.database.camera_collection", col), patch(
            "app.services.camera_access.is_admin", return_value=True
        ):
            page = await LocalVmsSource().list_cameras_public(
                _admin(), limit=2, offset=0
            )

        self.assertEqual(page["limit"], 2)
        self.assertEqual(page["returned"], 2)
        self.assertTrue(page["streams_not_auto_started"])
        for item in page["items"]:
            self.assertIsNone(item.get("password"))
            self.assertIsNone(item.get("main_rtsp_url"))
            self.assertTrue(item.get("location_path"))

    def test_public_camera_strips_secrets(self):
        pub = ccc_public_camera(
            {
                "_id": ObjectId(),
                "name": "X",
                "password": "p",
                "main_rtsp_url": "rtsp://x",
                "online": True,
                "location_path": "A / B",
            }
        )
        self.assertIsNone(pub["password"])
        self.assertIsNone(pub["main_rtsp_url"])
        self.assertEqual(pub["location_path"], "A / B")

    def test_client_media_no_rtsp(self):
        cleaned = sanitize_ccc_media_payload(
            {
                "live": {"ws_path": "/media/w1/api/ws"},
                "password": "x",
                "rtsp_url": "rtsp://bad",
            }
        )
        self.assertNotIn("password", cleaned)
        self.assertNotIn("rtsp_url", cleaned)
        self.assertTrue(cleaned["ccc_no_direct_camera"])


class HealthReports(unittest.IsolatedAsyncioTestCase):
    async def test_health_summary_bounded_and_real(self):
        with patch(
            "app.services.ccc_health_report_service._camera_fleet_row",
            new=AsyncMock(
                return_value={
                    "component_type": "camera_fleet",
                    "component": "Camera fleet",
                    "status": "degraded",
                    "last_check": "t",
                    "message": "total=10 online=8 offline=2",
                    "location": "",
                    "server_id": "",
                    "detail": "summary_only_no_per_camera_hydrate",
                    "password": None,
                    "rtsp_url": None,
                }
            ),
        ), patch(
            "app.services.ccc_health_report_service._vms_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "component_type": "vms_server",
                        "component": "VMS HA",
                        "status": "healthy",
                        "last_check": "t",
                        "message": "ok",
                        "location": "",
                        "server_id": "vms-1",
                        "detail": "",
                        "password": None,
                        "rtsp_url": None,
                    }
                ]
            ),
        ), patch(
            "app.services.ccc_health_report_service._recording_server_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "component_type": "recording_server",
                        "component": "Recording HA",
                        "status": "healthy",
                        "last_check": "t",
                        "message": "ok",
                        "location": "",
                        "server_id": "rec-1",
                        "detail": "",
                        "password": None,
                        "rtsp_url": None,
                    }
                ]
            ),
        ), patch(
            "app.services.ccc_health_report_service._go2rtc_rows",
            new=AsyncMock(
                return_value=[
                    {
                        "component_type": "go2rtc_worker",
                        "component": "go2rtc worker 1",
                        "status": "healthy",
                        "last_check": "t",
                        "message": "active",
                        "location": "",
                        "server_id": "1",
                        "detail": "/media/w{N}/api/ws",
                        "password": None,
                        "rtsp_url": None,
                    }
                ]
            ),
        ), patch(
            "app.services.ccc_health_report_service._storage_row",
            new=AsyncMock(
                return_value={
                    "component_type": "storage",
                    "component": "Recording storage",
                    "status": "healthy",
                    "last_check": "t",
                    "message": "total_gb=100 used_gb=40 free_gb=60",
                    "location": "",
                    "server_id": "",
                    "detail": "/rec",
                    "password": None,
                    "rtsp_url": None,
                }
            ),
        ), patch(
            "app.services.ccc_health_report_service._backend_row",
            return_value={
                "component_type": "backend",
                "component": "Backend readiness",
                "status": "healthy",
                "last_check": "t",
                "message": "ready=True",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._recording_subsystem_row",
            return_value={
                "component_type": "recording_subsystem",
                "component": "Recording subsystem",
                "status": "degraded",
                "last_check": "t",
                "message": "engine_enabled=False",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._ntp_row",
            return_value={
                "component_type": "ntp",
                "component": "OS network time (NTP)",
                "status": "healthy",
                "last_check": "t",
                "message": "sync_state=synchronized",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._failure_rows",
            new=AsyncMock(return_value=[]),
        ):
            data = await build_ccc_health_report(user=_admin(), limit=50, offset=0)

        self.assertTrue(data["rdso_18_6_14_health"])
        self.assertFalse(data["rdso_18_6_14_gis"])
        self.assertFalse(data["fabricated_metrics"])
        self.assertTrue(data["streams_not_started"])
        self.assertTrue(data["no_full_fleet_hydrate"])
        types = {r["component_type"] for r in data["items"]}
        self.assertIn("camera_fleet", types)
        self.assertIn("vms_server", types)
        self.assertIn("recording_server", types)
        self.assertIn("go2rtc_worker", types)
        self.assertIn("storage", types)
        self.assertIn("backend", types)
        self.assertNotIn("camera", types)  # no per-camera hydrate by default
        for row in data["items"]:
            self.assertIsNone(row.get("password"))
            self.assertIsNone(row.get("rtsp_url"))

    async def test_status_filter_degraded(self):
        with patch(
            "app.services.ccc_health_report_service._camera_fleet_row",
            new=AsyncMock(
                return_value={
                    "component_type": "camera_fleet",
                    "component": "Camera fleet",
                    "status": "degraded",
                    "last_check": "t",
                    "message": "m",
                    "location": "",
                    "server_id": "",
                    "detail": "",
                    "password": None,
                    "rtsp_url": None,
                }
            ),
        ), patch(
            "app.services.ccc_health_report_service._vms_rows",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.ccc_health_report_service._recording_server_rows",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.ccc_health_report_service._go2rtc_rows",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.ccc_health_report_service._storage_row",
            new=AsyncMock(
                return_value={
                    "component_type": "storage",
                    "component": "Storage",
                    "status": "healthy",
                    "last_check": "t",
                    "message": "m",
                    "location": "",
                    "server_id": "",
                    "detail": "",
                    "password": None,
                    "rtsp_url": None,
                }
            ),
        ), patch(
            "app.services.ccc_health_report_service._backend_row",
            return_value={
                "component_type": "backend",
                "component": "Backend",
                "status": "healthy",
                "last_check": "t",
                "message": "m",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._recording_subsystem_row",
            return_value={
                "component_type": "recording_subsystem",
                "component": "Rec",
                "status": "healthy",
                "last_check": "t",
                "message": "m",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._ntp_row",
            return_value={
                "component_type": "ntp",
                "component": "NTP",
                "status": "healthy",
                "last_check": "t",
                "message": "m",
                "location": "",
                "server_id": "",
                "detail": "",
                "password": None,
                "rtsp_url": None,
            },
        ), patch(
            "app.services.ccc_health_report_service._failure_rows",
            new=AsyncMock(return_value=[]),
        ):
            data = await build_ccc_health_report(
                user=_admin(), status="degraded", limit=50
            )
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["status"], "degraded")

    async def test_camera_component_paginated(self):
        rows = [
            {
                "component_type": "camera",
                "component": f"C{i}",
                "status": "healthy",
                "last_check": "t",
                "message": "registry online flag",
                "location": "L",
                "server_id": "",
                "detail": str(ObjectId()),
                "password": None,
                "rtsp_url": None,
            }
            for i in range(5)
        ]
        with patch(
            "app.services.ccc_health_report_service._paginated_camera_rows",
            new=AsyncMock(return_value=(rows[:2], 5)),
        ):
            data = await build_ccc_health_report(
                user=_admin(),
                component_type="camera",
                limit=2,
                offset=0,
            )
        self.assertEqual(data["returned"], 2)
        self.assertEqual(data["total"], 5)
        self.assertLessEqual(data["limit"], HEALTH_PAGE_MAX)
        self.assertTrue(all(r["component_type"] == "camera" for r in data["items"]))
        self.assertIsNone(data["items"][0].get("password"))

    def test_csv_export(self):
        items = [
            {
                "component_type": "storage",
                "component": "Storage",
                "status": "healthy",
                "last_check": "t",
                "message": "ok",
                "location": "",
                "server_id": "",
                "detail": "",
            }
        ]
        filename, body = export_health_csv(items)
        self.assertTrue(filename.endswith(".csv"))
        self.assertIn("component_type", body)
        self.assertIn("storage", body)
        self.assertNotIn("rtsp://", body)


if __name__ == "__main__":
    unittest.main()
