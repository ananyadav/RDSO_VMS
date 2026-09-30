"""Unit tests for RDSO 18.3.5 acceptance harness (no 128-stream load)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.rdso_128_acceptance import (
    ACCEPTANCE_ENV_FLAG,
    StreamSlot,
    acceptance_flag_enabled,
    build_acceptance_ffmpeg_cmd,
    count_duplicate_pids,
    require_acceptance_flag,
    run_128_stream_acceptance,
)
from app.services.recording_config import get_recording_capacity_info


class FlagAndCapacityTests(unittest.TestCase):
    def test_flag_default_off(self):
        with patch.dict(os.environ, {ACCEPTANCE_ENV_FLAG: ""}, clear=False):
            os.environ.pop(ACCEPTANCE_ENV_FLAG, None)
            self.assertFalse(acceptance_flag_enabled())

    def test_require_flag_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ACCEPTANCE_ENV_FLAG, None)
            with self.assertRaises(RuntimeError):
                require_acceptance_flag()

    def test_soft_cap_below_128_not_compliant(self):
        with patch("app.services.recording_config.RECORDING_MAX_CONCURRENT", 32), patch(
            "app.services.recording_config.RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS", 128
        ):
            info = get_recording_capacity_info()
        self.assertFalse(info["rdso_18_3_5_software_compliant"])
        self.assertEqual(info["rdso_min_simultaneous_streams"], 128)

    def test_unlimited_is_compliant(self):
        with patch("app.services.recording_config.RECORDING_MAX_CONCURRENT", 0):
            info = get_recording_capacity_info()
        self.assertTrue(info["rdso_18_3_5_software_compliant"])
        self.assertTrue(info["unlimited"])


class CmdAndDedupeTests(unittest.TestCase):
    def test_ffmpeg_cmd_is_stream_copy_short_hls(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = build_acceptance_ffmpeg_cmd(
                rtsp_url="rtsp://127.0.0.1:8554/cam_main",
                session_dir=Path(tmp),
                segment_seconds=2,
            )
        self.assertIn("-c:v", cmd)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertIn("-an", cmd)
        self.assertIn("rdso_18_3_5_128", " ".join(cmd))

    def test_duplicate_pid_detection(self):
        a = StreamSlot("1", "u1", "n", "10.0.0.1", "rtsp://x")
        b = StreamSlot("2", "u2", "n", "10.0.0.2", "rtsp://y")
        proc = MagicMock()
        proc.pid = 42
        a.process = proc
        b.process = proc
        self.assertEqual(count_duplicate_pids([a, b]), 1)


class AcceptanceGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_refuses_without_flag(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ACCEPTANCE_ENV_FLAG, None)
            with self.assertRaises(RuntimeError):
                await run_128_stream_acceptance(stream_count=2, duration_seconds=1)

    async def test_insufficient_cameras_fails_cleanly(self):
        with patch.dict(os.environ, {ACCEPTANCE_ENV_FLAG: "1"}):
            with patch(
                "app.services.rdso_128_acceptance.select_acceptance_cameras",
                new_callable=AsyncMock,
                return_value=[],
            ), patch(
                "app.services.rdso_128_acceptance.get_effective_recordings_dir",
                create=True,
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    with patch(
                        "app.services.storage_settings_store.get_effective_recordings_dir",
                        return_value=Path(tmp),
                    ):
                        report = await run_128_stream_acceptance(
                            stream_count=128,
                            duration_seconds=1,
                            acceptance_root=Path(tmp) / "run",
                        )
        self.assertFalse(report.passed)
        self.assertTrue(any("unique cameras" in r for r in report.fail_reasons))


if __name__ == "__main__":
    unittest.main()
