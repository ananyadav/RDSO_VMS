"""RDSO 18.3.14 — motion / activity based recording."""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.motion_capability import camera_has_dual_streams, detect_motion_capability
from app.services.motion_poller import parse_isapi_motion_active
from app.services.motion_recording_controller import (
    clear_motion_state,
    get_activity_state,
    get_stream_override,
    notify_motion_activity,
    refresh_camera_config,
    tick_idle_hold,
)
from app.services.motion_recording_types import ACTIVITY_ACTIVE, ACTIVITY_IDLE
from app.services.recording_config import resolve_recording_stream_choice


class DualStreamCapabilityTests(unittest.TestCase):
    def test_dual_stream_required(self):
        self.assertTrue(
            camera_has_dual_streams(
                {
                    "main_rtsp_url": "rtsp://a/main",
                    "sub_rtsp_url": "rtsp://a/sub",
                }
            )
        )
        self.assertFalse(
            camera_has_dual_streams(
                {
                    "main_rtsp_url": "rtsp://a/main",
                    "sub_rtsp_url": "rtsp://a/main",
                }
            )
        )


class CapabilityDetectTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_without_dual_stream(self):
        with patch(
            "app.services.motion_capability.build_camera_rtsp_urls",
            return_value={"main_rtsp_url": "rtsp://x", "sub_rtsp_url": ""},
        ):
            cap = await detect_motion_capability({"_id": "c1", "protocol": "HIKVISION"})
        self.assertFalse(cap["supported"])
        self.assertIn("Unsupported", cap["message"])

    async def test_signal_mode_supported_with_dual(self):
        with patch(
            "app.services.motion_capability.build_camera_rtsp_urls",
            return_value={
                "main_rtsp_url": "rtsp://x/main",
                "sub_rtsp_url": "rtsp://x/sub",
            },
        ):
            cap = await detect_motion_capability(
                {
                    "_id": "c1",
                    "protocol": "CUSTOM",
                    "motion_recording": {"motion_source": "signal"},
                }
            )
        self.assertTrue(cap["supported"])
        self.assertEqual(cap["protocol"], "signal")


class ActivityTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_motion_state()

    def tearDown(self):
        clear_motion_state()

    async def test_motion_then_idle_hold(self):
        await refresh_camera_config(
            "cam1",
            {
                "enabled": True,
                "active_stream": "main",
                "idle_stream": "sub",
                "hold_seconds": 5,
                "cooldown_seconds": 5,
            },
        )
        with patch(
            "app.services.motion_recording_controller._maybe_switch_stream",
            new_callable=AsyncMock,
            return_value=False,
        ):
            r1 = await notify_motion_activity("cam1", active=True, source="test")
            self.assertEqual(r1["state"]["activity"], ACTIVITY_ACTIVE)
            self.assertEqual(r1["state"]["desired_stream"], "main")

            from app.services import motion_recording_controller as ctl

            ctl._STATE["cam1"]["last_motion_at"] = time.time() - 60
            await tick_idle_hold("cam1")
            self.assertEqual(ctl._STATE["cam1"]["activity"], ACTIVITY_IDLE)
            self.assertEqual(ctl._STATE["cam1"]["desired_stream"], "sub")
            # After idle transition, override reflects idle stream once switch applies
            # (switch mocked) — desired is what matters for policy
            self.assertEqual(get_activity_state("cam1")["desired_stream"], "sub")

    async def test_cooldown_prevents_rapid_switch(self):
        await refresh_camera_config(
            "cam1",
            {
                "enabled": True,
                "active_stream": "main",
                "idle_stream": "sub",
                "hold_seconds": 5,
                "cooldown_seconds": 60,
            },
        )
        from app.services import motion_recording_controller as ctl

        ctl._STATE["cam1"]["current_stream"] = "sub"
        ctl._STATE["cam1"]["desired_stream"] = "main"
        ctl._STATE["cam1"]["last_switch_at"] = time.time()

        with patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.video_recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.video_recording.stop_camera_recording",
            new_callable=AsyncMock,
        ) as stop, patch(
            "app.services.video_recording.start_camera_recording",
            new_callable=AsyncMock,
        ) as start:
            switched = await ctl._maybe_switch_stream("cam1")
        self.assertFalse(switched)
        stop.assert_not_called()
        start.assert_not_called()

    async def test_switch_restarts_single_recorder(self):
        await refresh_camera_config(
            "cam1",
            {
                "enabled": True,
                "active_stream": "main",
                "idle_stream": "sub",
                "hold_seconds": 5,
                "cooldown_seconds": 1,
            },
        )
        from app.services import motion_recording_controller as ctl

        ctl._STATE["cam1"]["current_stream"] = "sub"
        ctl._STATE["cam1"]["desired_stream"] = "main"
        ctl._STATE["cam1"]["last_switch_at"] = 0

        with patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.video_recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.video_recording.ACTIVE_RECORDINGS",
            {},
        ), patch(
            "app.services.video_recording.stop_camera_recording",
            new_callable=AsyncMock,
        ) as stop, patch(
            "app.services.video_recording.start_camera_recording",
            new_callable=AsyncMock,
        ) as start:
            switched = await ctl._maybe_switch_stream("cam1")
        self.assertTrue(switched)
        stop.assert_awaited_once()
        start.assert_awaited_once()
        self.assertEqual(get_stream_override("cam1"), "main")


class ResolveStreamTests(unittest.TestCase):
    def setUp(self):
        clear_motion_state()

    def tearDown(self):
        clear_motion_state()

    def test_override_wins(self):
        from app.services import motion_recording_controller as ctl

        ctl._STATE["abc"] = {"enabled": True, "current_stream": "sub"}
        choice = resolve_recording_stream_choice({"_id": "abc", "recording_channel": "main"})
        self.assertEqual(choice, "sub")


class UnsupportedEnableTests(unittest.IsolatedAsyncioTestCase):
    async def test_enable_rejected_when_unsupported(self):
        from app.services.motion_recording_config import update_motion_recording_settings

        with patch(
            "app.services.motion_recording_config.camera_collection"
        ) as coll, patch(
            "app.services.motion_recording_config.detect_motion_capability",
            new_callable=AsyncMock,
            return_value={"supported": False, "message": "no dual"},
        ):
            coll.find_one = AsyncMock(return_value={"_id": "507f1f77bcf86cd799439011"})
            with self.assertRaises(ValueError):
                await update_motion_recording_settings(
                    "507f1f77bcf86cd799439011",
                    {"enabled": True},
                )


class RbacTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_require_admin(self):
        from app.routes.motion_recording import motion_settings_get_endpoint

        req = make_mocked_request("GET", "/api/cameras/x/motion-recording")
        with patch(
            "app.routes.motion_recording.deny_unless_admin",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "forbidden"}, status=403),
        ):
            resp = await motion_settings_get_endpoint(req)
        self.assertEqual(resp.status, 403)

    async def test_activity_ingest_requires_admin(self):
        from app.routes.motion_recording import motion_activity_ingest_endpoint

        req = make_mocked_request("POST", "/api/cameras/x/motion-activity")
        with patch(
            "app.routes.motion_recording.deny_unless_admin",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "forbidden"}, status=403),
        ):
            resp = await motion_activity_ingest_endpoint(req)
        self.assertEqual(resp.status, 403)


class IsapiParseTests(unittest.TestCase):
    def test_parse_active_markers(self):
        self.assertTrue(parse_isapi_motion_active("<IsMotion>true</IsMotion>"))
        self.assertFalse(parse_isapi_motion_active("<active>false</active>"))
        self.assertIsNone(parse_isapi_motion_active("<enabled>true</enabled>"))


class AlarmMotionHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_alarm_notifies_motion_controller(self):
        from app.services.alarm_rule_evaluator import process_alarm_signal
        from app.services.alarm_signal import NormalizedAlarmSignal
        from datetime import datetime, timezone

        signal = NormalizedAlarmSignal(
            camera_id="507f1f77bcf86cd799439011",
            camera_uid="ip_x",
            source_type="motion",
            occurred_at=datetime.now(timezone.utc),
            title="Motion",
            message="detected",
            metadata={"active": True},
        )
        with patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value={"_id": signal.camera_id, "camera_uid": "ip_x"},
        ), patch(
            "app.services.alarm_rule_evaluator.find_matching_enabled_rules",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.alarm_rule_evaluator._resolve_camera_uid",
            new_callable=AsyncMock,
            return_value="ip_x",
        ), patch(
            "app.services.motion_recording_controller.notify_motion_activity",
            new_callable=AsyncMock,
            return_value={"ok": True, "switched": False},
        ) as notify:
            out = await process_alarm_signal(signal)
        notify.assert_awaited()
        self.assertTrue(out.get("motion_activity", {}).get("ok"))


if __name__ == "__main__":
    unittest.main()
