"""RDSO 18.6.16.1 — CCC platform architecture / software overview evidence."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ccc_dashboard_prefs import get_dashboard_prefs, save_dashboard_prefs
from app.services.ccc_dashboard_service import get_ccc_dashboard
from app.services.ccc_incident_escalation import process_due_escalations
from app.services.ccc_sop_service import sop_to_public
from app.services.ccc_vms_source import (
    LocalVmsSource,
    ccc_capability_public,
    ccc_public_camera,
    sanitize_ccc_media_payload,
)
from app.services.vms_server_config import local_vms_server_id, vms_ha_enabled


def _admin():
    return {
        "id": "admin-16-1",
        "name": "Admin",
        "role": "Admin",
        "permissions": ["Events", "Live View"],
    }


class PlatformArchitectureEvidence(unittest.TestCase):
    def test_platform_architecture_block(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.16.1"])
        pa = cap["platform_architecture"]
        self.assertTrue(pa["rdso_18_6_16_1"])
        self.assertEqual(pa["browser_entry"], "/ccc")
        self.assertEqual(pa["capability_path"], "/api/ccc/capability")

        fd = pa["flexible_dynamic"]
        self.assertTrue(fd["runtime_config_without_restart"])
        self.assertTrue(fd["hot_reload_sop_escalation"])
        for path in (
            "/api/ccc/dashboard/prefs",
            "/api/ccc/sop-workflows",
            "/api/ccc/devices",
            "/api/ccc/admin/preemption-policy",
            "/api/ccc/admin/vms-integrations",
        ):
            self.assertIn(path, fd["surfaces"])

        dist = pa["distributed"]
        self.assertTrue(dist["vms_multi_node_ha"])
        self.assertTrue(dist["shared_mongo_state"])
        self.assertFalse(dist["single_node_only_assumption"])
        self.assertEqual(dist["vms_ha_env"], "VMS_HA_ENABLED")

        rt = pa["reactive_real_time"]
        self.assertEqual(rt["model"], "http_poll")
        self.assertFalse(rt["millisecond_realtime_claimed"])
        self.assertEqual(rt["dashboard_poll_seconds_suggested"], 15)
        self.assertEqual(rt["hot_screen_path"], "/api/ccc/hot-screen")
        self.assertEqual(rt["event_log_path"], "/api/ccc/event-log")

        sc = pa["scalable"]
        self.assertTrue(sc["paginated_lists"])
        self.assertTrue(sc["bounded_queries"])
        self.assertEqual(sc["max_live_tiles"], 16)
        self.assertFalse(sc["fleet_wide_stream_opening"])
        self.assertFalse(sc["hard_logical_camera_cap"])

        ip = pa["ip_network"]
        self.assertTrue(ip["browser_api_over_ip"])
        self.assertTrue(ip["relative_https_wss_capable_paths"])
        self.assertFalse(ip["proprietary_client_required"])

        wf = pa["automated_policies_workflows"]
        self.assertTrue(wf["sop_workflows"])
        self.assertTrue(wf["escalation_rules"])
        self.assertTrue(wf["alarm_display_switching"])
        self.assertTrue(wf["incident_automation"])
        self.assertFalse(wf["second_alarm_engine"])
        self.assertEqual(wf["escalation_process_path"], "/api/ccc/incidents/process-escalations")

        dash = pa["single_customized_dashboard"]
        self.assertTrue(dash["per_user_widget_visibility_order"])
        self.assertFalse(dash["fake_statistics"])
        self.assertEqual(dash["prefs_path"], "/api/ccc/dashboard/prefs")

    def test_no_secret_leakage_in_capability(self):
        cap = ccc_capability_public()
        blob = str(cap).lower()
        for needle in ("password", "rtsp://", "credential_encrypted", "api_key=", "bearer "):
            self.assertNotIn(needle, blob, needle)

    def test_public_camera_and_media_strip_secrets(self):
        pub = ccc_public_camera(
            {
                "_id": "c1",
                "name": "Gate",
                "password": "secret",
                "main_rtsp_url": "rtsp://cam/stream",
            }
        )
        self.assertIsNone(pub.get("password"))
        self.assertIsNone(pub.get("main_rtsp_url"))
        cleaned = sanitize_ccc_media_payload(
            {"live": {"ws_path": "/media/w1/api/ws"}, "password": "x", "rtsp_url": "rtsp://x"}
        )
        self.assertNotIn("password", cleaned)
        self.assertNotIn("rtsp_url", cleaned)
        self.assertTrue(cleaned["ccc_no_direct_camera"])


class RuntimeConfigWithoutRestart(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._store: dict[str, dict] = {}

        async def find_one(query):
            return self._store.get(query.get("user_id"))

        async def update_one(query, ops, upsert=False):
            uid = query["user_id"]
            doc = self._store.get(uid) or {"user_id": uid}
            if "$set" in ops:
                doc.update(ops["$set"])
            self._store[uid] = doc

        mock = MagicMock()
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)
        self._p = patch("app.services.ccc_dashboard_prefs.prefs_collection", mock)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_dashboard_prefs_persist_without_restart(self):
        saved = await save_dashboard_prefs(
            _admin(),
            widgets=[
                {"id": "cameras", "enabled": True, "order": 0},
                {"id": "events", "enabled": False, "order": 1},
            ],
        )
        self.assertEqual(saved["user_id"], "admin-16-1")
        loaded = await get_dashboard_prefs(_admin())
        enabled = {w["id"]: w["enabled"] for w in loaded["widgets"]}
        self.assertTrue(enabled.get("cameras"))
        self.assertFalse(enabled.get("events"))


class DistributedHaEvidence(unittest.TestCase):
    def test_vms_ha_config_surface_exists(self):
        self.assertIsInstance(vms_ha_enabled(), bool)
        self.assertIsInstance(local_vms_server_id(), str)
        cap = ccc_capability_public()
        self.assertTrue(cap["platform_architecture"]["distributed"]["shared_mongo_state"])
        self.assertIn("vms_servers", cap["management_reuses_existing"])


class ScalableBoundedLists(unittest.IsolatedAsyncioTestCase):
    async def test_local_source_list_cameras_bounded(self):
        src = LocalVmsSource()

        class _Cursor:
            def __init__(self):
                self._limit = None

            def sort(self, *a, **k):
                return self

            def skip(self, n):
                return self

            def limit(self, n):
                self._limit = n
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        cursor = _Cursor()
        mock_coll = MagicMock()
        mock_coll.count_documents = AsyncMock(return_value=5000)
        mock_coll.find = MagicMock(return_value=cursor)

        with patch("app.core.database.camera_collection", mock_coll), patch(
            "app.services.camera_access.is_admin", return_value=True
        ):
            page = await src.list_cameras_public(_admin(), limit=100, offset=0)

        self.assertEqual(page["limit"], 100)
        self.assertEqual(page["total"], 5000)
        self.assertTrue(page["streams_not_auto_started"])
        self.assertEqual(cursor._limit, 100)
        # Oversize request is clamped (LocalVmsSource max 500)
        with patch("app.core.database.camera_collection", mock_coll), patch(
            "app.services.camera_access.is_admin", return_value=True
        ):
            page2 = await src.list_cameras_public(_admin(), limit=9999, offset=0)
        self.assertEqual(page2["limit"], 500)
        self.assertEqual(cursor._limit, 500)

class WorkflowAutomationEvidence(unittest.TestCase):
    def test_sop_hot_reload_flag(self):
        pub = sop_to_public(
            {
                "_id": "x",
                "name": "Default",
                "enabled": True,
                "steps": [],
                "escalation_rules": [],
            }
        )
        self.assertTrue(pub["hot_reload"])

    def test_escalation_processor_exists(self):
        self.assertTrue(callable(process_due_escalations))
        from app.services import ccc_incident_escalation as esc

        self.assertIn("hot-reload", (esc.__doc__ or "").lower())


class EventToCccReactivePath(unittest.TestCase):
    def test_capability_documents_event_ccc_paths(self):
        pa = ccc_capability_public()["platform_architecture"]
        rt = pa["reactive_real_time"]
        self.assertEqual(rt["model"], "http_poll")
        self.assertEqual(rt["incidents_path"], "/api/ccc/incidents")
        self.assertEqual(rt["alarm_monitoring_path"], "/api/ccc/alarm-monitoring")
        # Events flow into existing pipeline; CCC reflects via poll surfaces
        self.assertTrue(
            ccc_capability_public()["incidents"]["linked_to_vms_events"]
        )


class DashboardPayloadClause(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_marks_16_1(self):
        with patch(
            "app.services.ccc_dashboard_service.get_ccc_status_snapshot",
            new=AsyncMock(
                return_value={
                    "recording": {},
                    "vms_ha": {"enabled": False},
                    "recording_ha": {},
                    "go2rtc": {},
                    "users_sessions": {},
                }
            ),
        ), patch(
            "app.services.ccc_dashboard_service._camera_counts",
            new=AsyncMock(return_value={"total": 0, "online": 0}),
        ), patch(
            "app.services.ccc_dashboard_service._event_counts",
            new=AsyncMock(return_value={"open": 0}),
        ), patch(
            "app.services.ccc_dashboard_service._incident_counts",
            new=AsyncMock(return_value={"open": 0}),
        ), patch(
            "app.services.ccc_dashboard_service._recent_activity",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.ccc_dashboard_service._storage_summary",
            new=AsyncMock(return_value={"ok": True}),
        ):
            dash = await get_ccc_dashboard(user=_admin())
        self.assertTrue(dash["rdso_18_6_16_1"])
        self.assertFalse(dash["fake_statistics"])
        self.assertEqual(dash["poll_seconds_suggested"], 15)


if __name__ == "__main__":
    unittest.main()
