"""Tests for alarm-triggered recording service."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services import recording_schedule_store as recording_sched
from app.services.alarm_recording_service import (
    is_alarm_owned_recording,
    reset_alarm_recording_for_tests,
    start_alarm_triggered_recording,
)
from app.services.alarm_rule_service import AlarmRuleValidationError, validate_rule_payload

CAMERA_ID = "507f1f77bcf86cd799439011"
EVENT_ID = "507f1f77bcf86cd799439015"
RULE_ID = "507f1f77bcf86cd799439013"
SESSION_ID = "507f1f77bcf86cd799439016"


class AlarmRuleRecordingValidationTests(unittest.TestCase):
    def test_start_recording_requires_duration(self):
        with self.assertRaises(AlarmRuleValidationError):
            validate_rule_payload(
                {
                    "name": "Rec",
                    "enabled": True,
                    "camera_id": CAMERA_ID,
                    "trigger": {"source_type": "signal_loss"},
                    "actions": ["create_event", "start_recording"],
                    "severity": "warning",
                    "cooldown_seconds": 60,
                }
            )

    def test_start_recording_valid_duration(self):
        out = validate_rule_payload(
            {
                "name": "Rec",
                "enabled": True,
                "camera_id": CAMERA_ID,
                "trigger": {"source_type": "signal_loss"},
                "actions": ["create_event", "start_recording"],
                "severity": "warning",
                "cooldown_seconds": 60,
                "recording": {"duration_seconds": 60},
            }
        )
        self.assertEqual(out["recording"], {"duration_seconds": 60})

    def test_invalid_duration_rejected(self):
        with self.assertRaises(AlarmRuleValidationError):
            validate_rule_payload(
                {
                    "name": "Rec",
                    "enabled": True,
                    "camera_id": CAMERA_ID,
                    "trigger": {"source_type": "signal_loss"},
                    "actions": ["start_recording"],
                    "severity": "warning",
                    "cooldown_seconds": 60,
                    "recording": {"duration_seconds": 2},
                }
            )

    def test_existing_actions_without_recording_config(self):
        out = validate_rule_payload(
            {
                "name": "Evt",
                "enabled": True,
                "camera_id": CAMERA_ID,
                "trigger": {"source_type": "signal_loss"},
                "actions": ["create_event", "ui_notification"],
                "severity": "warning",
                "cooldown_seconds": 60,
            }
        )
        self.assertNotIn("recording", out)


class AlarmRecordingServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_alarm_recording_for_tests()

    def tearDown(self):
        reset_alarm_recording_for_tests()

    async def test_master_disabled(self):
        with patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True), patch.object(
            recording_sched, "master_enabled", False
        ):
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
            )
        self.assertEqual(result["recording_status"], "master_disabled")

    async def test_engine_disabled(self):
        with patch.object(recording_sched, "master_enabled", True), patch(
            "app.services.alarm_recording_service.is_recording_engine_enabled", return_value=False
        ):
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
            )
        self.assertEqual(result["recording_status"], "engine_disabled")

    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=False)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_new_alarm_recording(self, _eng, _is_rec, mock_start, mock_update):
        with patch.object(recording_sched, "master_enabled", True):
            mock_start.return_value = {"id": SESSION_ID}
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
            )
        self.assertEqual(result["recording_status"], "started")
        self.assertEqual(result["recording_session_id"], SESSION_ID)
        self.assertTrue(is_alarm_owned_recording(CAMERA_ID))
        mock_update.assert_awaited()
        mock_start.assert_awaited_once_with(CAMERA_ID)

    @patch("app.services.alarm_recording_service.get_active_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=True)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_reuse_existing_normal_recording(self, _eng, _is_rec, mock_active):
        with patch.object(recording_sched, "master_enabled", True):
            mock_active.return_value = {"id": SESSION_ID}
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
            )
        self.assertEqual(result["recording_status"], "already_recording")
        self.assertEqual(result["recording_session_id"], SESSION_ID)
        self.assertFalse(is_alarm_owned_recording(CAMERA_ID))

    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.get_active_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=True)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_extend_alarm_owned_recording(self, _eng, _is_rec, mock_active, _update):
        from app.services import alarm_recording_service as ars

        with patch.object(recording_sched, "master_enabled", True):
            mock_active.return_value = {"id": SESSION_ID}
            ars._alarm_owned[CAMERA_ID] = {
                "session_id": SESSION_ID,
                "event_id": EVENT_ID,
                "rule_id": RULE_ID,
                "auto_stop_at": datetime.now(timezone.utc),
                "stop_task": asyncio.create_task(asyncio.sleep(60)),
            }
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=45,
            )
        self.assertEqual(result["recording_status"], "extended")
        self.assertEqual(result["recording_session_id"], SESSION_ID)

    @patch("app.services.alarm_recording_service.stop_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=True)
    async def test_auto_stop_alarm_owned(self, mock_is_rec, mock_stop):
        from app.services import alarm_recording_service as ars

        ars._alarm_owned[CAMERA_ID] = {
            "session_id": SESSION_ID,
            "event_id": EVENT_ID,
            "rule_id": RULE_ID,
            "auto_stop_at": datetime.now(timezone.utc),
            "stop_task": None,
        }
        await ars._schedule_auto_stop(CAMERA_ID, SESSION_ID, datetime.now(timezone.utc))
        await asyncio.sleep(0.05)
        mock_stop.assert_awaited_once_with(CAMERA_ID)
        self.assertFalse(is_alarm_owned_recording(CAMERA_ID))

    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=False)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_start_failure(self, _eng, _is_rec, mock_start):
        with patch.object(recording_sched, "master_enabled", True):
            mock_start.side_effect = RuntimeError("ffmpeg failed")
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
            )
        self.assertEqual(result["recording_status"], "failed")


if __name__ == "__main__":
    unittest.main()
