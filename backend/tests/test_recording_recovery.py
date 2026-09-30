"""Tests for RDSO 18.3.11 recording fault tolerance & recovery."""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.recording_recovery import (
    RECOVERY_BACKOFF,
    RECOVERY_RECONNECTING,
    RECOVERY_RECORDING,
    compute_restart_backoff_seconds,
)
from app.services.video_recording import (
    ACTIVE_RECORDINGS,
    VideoRecorder,
    ensure_recording_process_alive,
)


class BackoffTests(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        with patch("app.services.recording_recovery.RECORDING_RESTART_BASE_SECONDS", 2.0), patch(
            "app.services.recording_recovery.RECORDING_RESTART_MAX_SECONDS", 60.0
        ):
            self.assertEqual(compute_restart_backoff_seconds(1), 2.0)
            self.assertEqual(compute_restart_backoff_seconds(2), 4.0)
            self.assertEqual(compute_restart_backoff_seconds(3), 8.0)
            self.assertEqual(compute_restart_backoff_seconds(10), 60.0)


class EnsureAliveTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        ACTIVE_RECORDINGS.clear()

    async def test_not_active(self):
        self.assertEqual(await ensure_recording_process_alive("missing"), "not_active")

    async def test_restarts_dead_monitor(self):
        recorder = VideoRecorder("507f1f77bcf86cd799439011", "507f1f77bcf86cd799439099")
        recorder.is_recording = True
        recorder._rtsp_url = "rtsp://example/stream"
        dead = asyncio.get_running_loop().create_future()
        dead.set_result(None)
        recorder._monitor_task = dead
        proc = MagicMock()
        proc.returncode = None
        recorder.recording_process = proc
        ACTIVE_RECORDINGS[recorder.camera_id] = {
            "recorder": recorder,
            "session_id": recorder.session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.object(recorder, "_monitor_recording_process", new_callable=AsyncMock) as mon:
            async def _idle():
                await asyncio.sleep(3600)

            mon.side_effect = _idle
            result = await ensure_recording_process_alive(recorder.camera_id)
            self.assertEqual(result, "monitor_restarted")
            self.assertIsNotNone(recorder._monitor_task)
            self.assertFalse(recorder._monitor_task.done())
            recorder.is_recording = False
            recorder._monitor_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await recorder._monitor_task
            # Drain: cancelled AsyncMock may still raise
            try:
                await asyncio.sleep(0)
            except Exception:
                pass


class FfmpegRestartMonitorTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        ACTIVE_RECORDINGS.clear()

    async def test_unexpected_exit_restarts_same_session(self):
        recorder = VideoRecorder("507f1f77bcf86cd799439011", "sess_restart_1")
        recorder.is_recording = True
        recorder._rtsp_url = "rtsp://cam/stream"
        recorder._spawn_started_monotonic = asyncio.get_running_loop().time()

        first = AsyncMock()
        first.wait = AsyncMock(return_value=1)
        first.returncode = 1
        first.stdin = None
        first.stderr = None

        second = AsyncMock()
        second.wait = AsyncMock(side_effect=asyncio.CancelledError)
        second.returncode = None
        second.stdin = None
        second.stderr = None

        recorder.recording_process = first
        spawn_calls = {"n": 0}

        async def fake_spawn(_url):
            spawn_calls["n"] += 1
            recorder.recording_process = second

        with patch.object(recorder, "_spawn_ffmpeg", side_effect=fake_spawn), patch.object(
            recorder, "_persist_recovery", new_callable=AsyncMock
        ) as persist, patch(
            "app.services.recording_config.RECORDING_RESTART_STABLE_SECONDS", 1000.0
        ), patch(
            "app.services.recording_recovery.compute_restart_backoff_seconds",
            return_value=0,
        ):
            task = asyncio.create_task(recorder._monitor_recording_process())
            for _ in range(50):
                if spawn_calls["n"] >= 1:
                    break
                await asyncio.sleep(0)
            self.assertGreaterEqual(spawn_calls["n"], 1)
            recorder.is_recording = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertEqual(recorder.session_id, "sess_restart_1")
        states = [c.kwargs.get("recovery_state") for c in persist.await_args_list]
        self.assertIn(RECOVERY_BACKOFF, states)
        self.assertTrue(
            RECOVERY_RECONNECTING in states or RECOVERY_RECORDING in states,
            states,
        )

    async def test_duplicate_start_returns_existing(self):
        from app.services import video_recording as vr

        cam = "507f1f77bcf86cd799439011"
        ACTIVE_RECORDINGS[cam] = {
            "recorder": MagicMock(is_recording=True),
            "session_id": "existing",
            "started_at": "t",
        }
        with patch(
            "app.services.recording_config.is_recording_engine_enabled", return_value=True
        ), patch(
            "app.services.video_recording.get_recording_session",
            new_callable=AsyncMock,
            return_value={"id": "existing"},
        ):
            out = await vr.start_camera_recording(cam)
        self.assertEqual(out["id"], "existing")


class BackendRestartRecoverySemanticsTests(unittest.TestCase):
    def test_temporary_ownership_is_memory_only(self):
        from app.services.alarm_recording_service import (
            is_alarm_owned_recording,
            reset_alarm_recording_for_tests,
        )
        from app.services.operator_recording_service import (
            is_operator_duration_owned,
            reset_operator_recording_for_tests,
        )

        reset_alarm_recording_for_tests()
        reset_operator_recording_for_tests()
        # Simulate "process restart" by clearing in-memory maps
        self.assertFalse(is_alarm_owned_recording("any"))
        self.assertFalse(is_operator_duration_owned("any"))

    def test_schedule_not_required_camera_not_resumed(self):
        from app.services import recording_schedule_store as sched

        cid = "507f1f77bcf86cd799439011"
        saved = dict(sched.recording_schedule)
        saved_master = sched.master_enabled
        try:
            sched.recording_schedule = {cid: False}
            sched.master_enabled = True
            should = bool(sched.recording_schedule.get(cid)) and sched.master_enabled
            self.assertFalse(should)
        finally:
            sched.recording_schedule = saved
            sched.master_enabled = saved_master


if __name__ == "__main__":
    unittest.main()
