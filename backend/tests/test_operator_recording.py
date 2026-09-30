"""Tests for operator duration recording + route control (RDSO 18.1.14)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.recording import (
    get_recording_status_endpoint,
    start_recording_endpoint,
    stop_recording_endpoint,
    toggle_recording_endpoint,
)
from app.services.operator_recording_service import (
    is_operator_duration_owned,
    is_temporary_owned_recording,
    reset_operator_recording_for_tests,
    start_operator_duration_recording,
)
from app.services import recording_schedule_store as recording_sched

CAMERA_ID = "507f1f77bcf86cd799439011"
SESSION_ID = "507f1f77bcf86cd799439099"


def _request(method: str, path: str, match_info=None, json_body=None):
    request = make_mocked_request(method, path, match_info=match_info or {})

    async def _json():
        return json_body if json_body is not None else {}

    request.json = _json  # type: ignore[method-assign]
    return request


class TestOperatorDurationRecording(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_operator_recording_for_tests()

    def tearDown(self):
        reset_operator_recording_for_tests()

    async def test_start_duration_and_owns_session(self):
        session = {"id": SESSION_ID, "camera_id": CAMERA_ID, "status": "recording"}
        with patch(
            "app.services.operator_recording_service.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.operator_recording_service.is_camera_recording",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "app.services.operator_recording_service.start_camera_recording",
            new_callable=AsyncMock,
            return_value=session,
        ) as start, patch(
            "app.services.operator_recording_service.update_recording_session",
            new_callable=AsyncMock,
        ):
            result = await start_operator_duration_recording(CAMERA_ID, duration_seconds=30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "started")
        self.assertTrue(is_operator_duration_owned(CAMERA_ID))
        start.assert_awaited_once_with(CAMERA_ID)
        from app.services.operator_recording_service import release_operator_duration_ownership

        release_operator_duration_ownership(CAMERA_ID)
        self.assertFalse(is_operator_duration_owned(CAMERA_ID))

    async def test_engine_disabled(self):
        with patch(
            "app.services.operator_recording_service.is_recording_engine_enabled",
            return_value=False,
        ):
            result = await start_operator_duration_recording(CAMERA_ID, duration_seconds=30)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "engine_disabled")

    async def test_does_not_take_over_non_duration_session(self):
        with patch(
            "app.services.operator_recording_service.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.operator_recording_service.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.operator_recording_service.get_active_recording_session",
            new_callable=AsyncMock,
            return_value={"id": SESSION_ID},
        ), patch(
            "app.services.operator_recording_service.start_camera_recording",
            new_callable=AsyncMock,
        ) as start:
            result = await start_operator_duration_recording(CAMERA_ID, duration_seconds=30)
        self.assertEqual(result["status"], "already_recording")
        start.assert_not_awaited()

    async def test_temporary_owned_includes_alarm(self):
        with patch(
            "app.services.alarm_recording_service.is_alarm_owned_recording",
            return_value=True,
        ):
            self.assertTrue(is_temporary_owned_recording(CAMERA_ID))


class TestRecordingControlRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_operator_recording_for_tests()
        recording_sched.recording_schedule = {CAMERA_ID: False}
        recording_sched.master_enabled = False

    def tearDown(self):
        reset_operator_recording_for_tests()
        recording_sched.recording_schedule = {}
        recording_sched.master_enabled = False

    async def test_start_duration_mode(self):
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.routes.recording._audit_recording_config",
            new_callable=AsyncMock,
        ), patch(
            "app.services.operator_recording_service.start_operator_duration_recording",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "status": "started",
                "session_id": SESSION_ID,
                "session": {"id": SESSION_ID},
                "auto_stop_at": "2026-09-07T12:00:30+00:00",
                "duration_seconds": 30,
            },
        ) as start_dur:
            req = _request(
                "POST",
                f"/api/recordings/{CAMERA_ID}/start",
                match_info={"cameraId": CAMERA_ID},
                json_body={"duration_seconds": 30},
            )
            resp = await start_recording_endpoint(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertEqual(body["mode"], "duration")
        self.assertEqual(body["duration_seconds"], 30)
        start_dur.assert_awaited_once()
        # Duration mode must not leave continuous schedule on
        self.assertFalse(recording_sched.recording_schedule.get(CAMERA_ID, False))

    async def test_start_duration_invalid(self):
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.is_recording_engine_enabled",
            return_value=True,
        ):
            req = _request(
                "POST",
                f"/api/recordings/{CAMERA_ID}/start",
                match_info={"cameraId": CAMERA_ID},
                json_body={"duration_seconds": 2},
            )
            resp = await start_recording_endpoint(req)
        self.assertEqual(resp.status, 400)

    async def test_toggle_starts_immediately(self):
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.routes.recording.recording_sched.save_recording_settings",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording._audit_recording_config",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording.start_camera_recording",
            new_callable=AsyncMock,
            return_value={"id": SESSION_ID},
        ) as start, patch(
            "app.routes.recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ):
            req = _request(
                "POST",
                f"/api/recordings/{CAMERA_ID}/toggle",
                match_info={"cameraId": CAMERA_ID},
            )
            resp = await toggle_recording_endpoint(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["recording"])
        self.assertTrue(body["active"])
        self.assertEqual(body["mode"], "continuous")
        start.assert_awaited_once_with(CAMERA_ID)

    async def test_toggle_stop_preserves_temporary_owned(self):
        recording_sched.recording_schedule[CAMERA_ID] = True
        recording_sched.master_enabled = True
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.routes.recording.recording_sched.save_recording_settings",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording._audit_recording_config",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.operator_recording_service.is_temporary_owned_recording",
            return_value=True,
        ), patch(
            "app.routes.recording.stop_camera_recording",
            new_callable=AsyncMock,
        ) as stop:
            req = _request(
                "POST",
                f"/api/recordings/{CAMERA_ID}/toggle",
                match_info={"cameraId": CAMERA_ID},
            )
            resp = await toggle_recording_endpoint(req)
        self.assertEqual(resp.status, 200)
        self.assertFalse(json.loads(resp.text)["scheduled"])
        stop.assert_not_awaited()

    async def test_stop_clears_schedule_and_ownership(self):
        recording_sched.recording_schedule[CAMERA_ID] = True
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.recording_sched.save_recording_settings",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording._audit_recording_config",
            new_callable=AsyncMock,
        ), patch(
            "app.routes.recording.stop_camera_recording",
            new_callable=AsyncMock,
            return_value={"id": SESSION_ID, "status": "stopped"},
        ), patch(
            "app.services.operator_recording_service.release_operator_duration_ownership",
        ) as release:
            req = _request(
                "POST",
                f"/api/recordings/{CAMERA_ID}/stop",
                match_info={"cameraId": CAMERA_ID},
            )
            resp = await stop_recording_endpoint(req)
        self.assertEqual(resp.status, 200)
        self.assertFalse(recording_sched.recording_schedule.get(CAMERA_ID, False))
        release.assert_called_once()

    async def test_status_includes_mode_flags(self):
        recording_sched.recording_schedule[CAMERA_ID] = True
        recording_sched.master_enabled = True
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.recording.get_camera_hls_info",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.routes.recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.routes.recording.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.routes.recording.get_active_recording_session",
            new_callable=AsyncMock,
            return_value={"id": SESSION_ID, "start_reason": "manual"},
        ), patch(
            "app.services.alarm_recording_service.is_alarm_owned_recording",
            return_value=False,
        ), patch(
            "app.services.operator_recording_service.is_operator_duration_owned",
            return_value=False,
        ):
            req = _request(
                "GET",
                f"/api/recordings/{CAMERA_ID}/status",
                match_info={"cameraId": CAMERA_ID},
            )
            resp = await get_recording_status_endpoint(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["active"])
        self.assertTrue(body["scheduled"])
        self.assertEqual(body["mode"], "continuous")


class TestScheduleMonitorTemporaryOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_skips_temporary_owned(self):
        """Unit-level: temporary ownership gate used by monitor."""
        with patch(
            "app.services.alarm_recording_service.is_alarm_owned_recording",
            return_value=False,
        ), patch(
            "app.services.operator_recording_service.is_operator_duration_owned",
            return_value=True,
        ):
            self.assertTrue(is_temporary_owned_recording(CAMERA_ID))


if __name__ == "__main__":
    unittest.main()
