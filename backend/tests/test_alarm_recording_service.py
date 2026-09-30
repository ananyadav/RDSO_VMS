"""Tests for alarm-triggered recording with pre/post alarm windows."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import recording_schedule_store as recording_sched
from app.services.alarm_recording_service import (
    is_alarm_owned_recording,
    reset_alarm_recording_for_tests,
    start_alarm_triggered_recording,
)
from app.services.alarm_rule_service import AlarmRuleValidationError, validate_rule_payload
from app.services.instant_replay_snapshot import snapshot_instant_replay_pre_alarm

CAMERA_ID = "507f1f77bcf86cd799439011"
EVENT_ID = "507f1f77bcf86cd799439015"
RULE_ID = "507f1f77bcf86cd799439013"
SESSION_ID = "507f1f77bcf86cd799439016"
CAMERA_DOC = {
    "_id": CAMERA_ID,
    "ip_address": "10.0.0.9",
    "camera_uid": "ip_10_0_0_9",
}


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
        self.assertEqual(
            out["recording"],
            {
                "pre_alarm_seconds": 0,
                "post_alarm_seconds": 60,
                "duration_seconds": 60,
            },
        )

    def test_pre_and_post_alarm_config(self):
        out = validate_rule_payload(
            {
                "name": "Rec",
                "enabled": True,
                "camera_id": CAMERA_ID,
                "trigger": {"source_type": "signal_loss"},
                "actions": ["start_recording"],
                "severity": "warning",
                "cooldown_seconds": 60,
                "recording": {"pre_alarm_seconds": 15, "post_alarm_seconds": 90},
            }
        )
        self.assertEqual(out["recording"]["pre_alarm_seconds"], 15)
        self.assertEqual(out["recording"]["post_alarm_seconds"], 90)
        self.assertEqual(out["recording"]["duration_seconds"], 90)

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

    def test_invalid_pre_alarm_rejected(self):
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
                    "recording": {"pre_alarm_seconds": 9999, "post_alarm_seconds": 30},
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


class InstantReplayPreAlarmSnapshotTests(unittest.TestCase):
    def test_copies_real_segments_into_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = Path(tmp) / "buf"
            dest = Path(tmp) / "pre"
            buf.mkdir()
            now = datetime.now(timezone.utc)
            for i in range(5):
                seg = buf / f"seg_{i:05d}.ts"
                seg.write_bytes(b"\x00" * 64)
                # Stagger mtimes into the past
                ts = (now - timedelta(seconds=(5 - i) * 2)).timestamp()
                import os

                os.utime(seg, (ts, ts))

            with patch(
                "app.services.instant_replay_snapshot.INSTANT_REPLAY_ENABLED", True
            ), patch(
                "app.services.instant_replay_snapshot._BUFFERS",
                {"ip_10_0_0_9": {"dir": buf}},
            ), patch(
                "app.services.instant_replay_snapshot.INSTANT_REPLAY_SEGMENT_SECONDS", 2
            ):
                result = snapshot_instant_replay_pre_alarm(
                    "ip_10_0_0_9",
                    pre_alarm_seconds=6,
                    dest_dir=dest,
                    event_time=now,
                )

            self.assertIn(result["status"], ("ok", "partial"))
            self.assertGreater(result["segment_count"], 0)
            self.assertTrue((dest / "index.m3u8").is_file())
            self.assertTrue(any(dest.glob("seg_*.ts")))

    def test_unavailable_when_buffer_cold(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "pre"
            with patch(
                "app.services.instant_replay_snapshot.INSTANT_REPLAY_ENABLED", True
            ), patch("app.services.instant_replay_snapshot._BUFFERS", {}), patch(
                "app.services.instant_replay_snapshot.buffer_dir",
                return_value=Path(tmp) / "missing",
            ):
                result = snapshot_instant_replay_pre_alarm(
                    "ip_x",
                    pre_alarm_seconds=10,
                    dest_dir=dest,
                )
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["segment_count"], 0)


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

    @patch("app.services.alarm_recording_service.get_camera_by_ref", new_callable=AsyncMock, return_value=CAMERA_DOC)
    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=False)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_new_alarm_recording(self, _eng, _is_rec, mock_start, mock_update, _cam):
        with patch.object(recording_sched, "master_enabled", True):
            mock_start.return_value = {
                "id": SESSION_ID,
                "storage_path": f"ip_10_0_0_9/sessions/{SESSION_ID}",
                "camera_uid": "ip_10_0_0_9",
            }
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
                pre_alarm_seconds=0,
            )
        self.assertEqual(result["recording_status"], "started")
        self.assertEqual(result["recording_session_id"], SESSION_ID)
        self.assertTrue(is_alarm_owned_recording(CAMERA_ID))
        mock_update.assert_awaited()
        mock_start.assert_awaited_once_with(CAMERA_ID)
        self.assertEqual(result.get("pre_alarm", {}).get("status"), "skipped")

    @patch("app.services.alarm_recording_service.get_camera_by_ref", new_callable=AsyncMock, return_value=CAMERA_DOC)
    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=False)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_pre_alarm_attached_to_new_session(self, _eng, _is_rec, mock_start, mock_update, _cam):
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            with patch.object(recording_sched, "master_enabled", True), patch(
                "app.services.alarm_recording_service.get_effective_recordings_dir",
                return_value=recordings,
            ), patch(
                "app.services.alarm_recording_service.snapshot_instant_replay_pre_alarm",
                return_value={
                    "status": "ok",
                    "source": "instant_replay_buffer",
                    "seconds_requested": 10,
                    "seconds_captured": 10,
                    "segment_count": 5,
                    "path": str(Path(tmp) / "hold"),
                },
            ) as mock_snap:
                # Seed a fake hold copy source: freeze writes into pre_hold; snapshot mocked
                # so we also write a marker file via side effect
                def _snap(*_args, **kwargs):
                    dest = kwargs["dest_dir"]
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "seg_00000.ts").write_bytes(b"REALPRE")
                    (dest / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
                    return {
                        "status": "ok",
                        "source": "instant_replay_buffer",
                        "seconds_requested": 10,
                        "seconds_captured": 10,
                        "segment_count": 1,
                        "path": str(dest),
                    }

                mock_snap.side_effect = _snap
                mock_start.return_value = {
                    "id": SESSION_ID,
                    "storage_path": f"ip_10_0_0_9/sessions/{SESSION_ID}",
                    "camera_uid": "ip_10_0_0_9",
                }
                result = await start_alarm_triggered_recording(
                    CAMERA_ID,
                    event_id=EVENT_ID,
                    rule_id=RULE_ID,
                    source_type="signal_loss",
                    pre_alarm_seconds=10,
                    post_alarm_seconds=20,
                )

            pre_dir = recordings / "ip_10_0_0_9" / "sessions" / SESSION_ID / "pre_alarm"
            self.assertEqual(result["recording_status"], "started")
            self.assertEqual(result["pre_alarm"]["status"], "ok")
            self.assertTrue((pre_dir / "seg_00000.ts").is_file())
            self.assertEqual((pre_dir / "seg_00000.ts").read_bytes(), b"REALPRE")
            meta_call = mock_update.await_args
            self.assertEqual(meta_call.args[0], SESSION_ID)
            self.assertEqual(meta_call.args[1].get("pre_alarm_seconds"), 10)
            self.assertEqual(meta_call.args[1].get("post_alarm_seconds"), 20)

    @patch("app.services.alarm_recording_service.get_camera_by_ref", new_callable=AsyncMock, return_value=CAMERA_DOC)
    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.get_active_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=True)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_reuse_existing_normal_recording(self, _eng, _is_rec, mock_active, mock_start, _update, _cam):
        with patch.object(recording_sched, "master_enabled", True):
            mock_active.return_value = {
                "id": SESSION_ID,
                "storage_path": f"ip_10_0_0_9/sessions/{SESSION_ID}",
                "camera_uid": "ip_10_0_0_9",
            }
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
                pre_alarm_seconds=0,
            )
        self.assertEqual(result["recording_status"], "already_recording")
        self.assertEqual(result["recording_session_id"], SESSION_ID)
        self.assertFalse(is_alarm_owned_recording(CAMERA_ID))
        mock_start.assert_not_awaited()

    @patch("app.services.alarm_recording_service.get_camera_by_ref", new_callable=AsyncMock, return_value=CAMERA_DOC)
    @patch("app.services.alarm_recording_service.update_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.get_active_recording_session", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=True)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_extend_alarm_owned_recording(self, _eng, _is_rec, mock_active, mock_start, _update, _cam):
        from app.services import alarm_recording_service as ars

        with patch.object(recording_sched, "master_enabled", True):
            mock_active.return_value = {"id": SESSION_ID, "camera_uid": "ip_10_0_0_9"}
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
                pre_alarm_seconds=0,
            )
        self.assertEqual(result["recording_status"], "extended")
        self.assertEqual(result["recording_session_id"], SESSION_ID)
        mock_start.assert_not_awaited()
        entry = ars._alarm_owned[CAMERA_ID]
        self.assertGreater(
            entry["auto_stop_at"],
            datetime.now(timezone.utc) + timedelta(seconds=40),
        )

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

    @patch("app.services.alarm_recording_service.get_camera_by_ref", new_callable=AsyncMock, return_value=CAMERA_DOC)
    @patch("app.services.alarm_recording_service.start_camera_recording", new_callable=AsyncMock)
    @patch("app.services.alarm_recording_service.is_camera_recording", new_callable=AsyncMock, return_value=False)
    @patch("app.services.alarm_recording_service.is_recording_engine_enabled", return_value=True)
    async def test_start_failure(self, _eng, _is_rec, mock_start, _cam):
        with patch.object(recording_sched, "master_enabled", True):
            mock_start.side_effect = RuntimeError("ffmpeg failed")
            result = await start_alarm_triggered_recording(
                CAMERA_ID,
                event_id=EVENT_ID,
                rule_id=RULE_ID,
                source_type="signal_loss",
                duration_seconds=30,
                pre_alarm_seconds=0,
            )
        self.assertEqual(result["recording_status"], "failed")


if __name__ == "__main__":
    unittest.main()
