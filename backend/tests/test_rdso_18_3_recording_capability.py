"""RDSO 18.3.1 / 18.3.2 / 18.3.5 / 18.3.6 capability tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.app_timezone import clear_app_timezone_cache, get_system_time_status
from app.services.recording_config import get_recording_capacity_info, get_recording_stream_info
from app.services.recording_media import build_recording_media_response
from app.services.video_recording import ACTIVE_RECORDINGS, VideoRecorder, start_camera_recording


class CodecAudioCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_stream_info_declares_h264_h265_and_audio(self):
        info = get_recording_stream_info()
        self.assertEqual(info["codec_mode"], "copy_or_mjpeg_encode")
        self.assertIn("H.264", info["video_codecs_supported"])
        self.assertIn("H.265", info["video_codecs_supported"])
        self.assertIn("MJPEG", info["video_codecs_supported"])
        self.assertIn(info["audio_mode"], ("aac_when_present", "disabled"))

    async def test_ffmpeg_cmd_maps_optional_audio_when_enabled(self):
        recorder = VideoRecorder("507f1f77bcf86cd799439011", "sess_audio")
        captured = {}

        async def fake_exec(*cmd, **_kwargs):
            captured["cmd"] = list(cmd)
            proc = MagicMock()
            proc.stderr = None
            proc.stdin = None
            proc.returncode = None
            return proc

        with patch(
            "app.services.recording_codec.probe_recording_video_codec",
            new_callable=AsyncMock,
            return_value={"ok": True, "codec_name": "h264", "video_mode": "copy"},
        ), patch("app.services.recording_config.RECORDING_AUDIO_ENABLED", True), patch(
            "asyncio.create_subprocess_exec", side_effect=fake_exec
        ), patch.object(recorder, "_read_ffmpeg_stderr", new_callable=AsyncMock), patch(
            "app.services.video_recording.update_recording_session", new_callable=AsyncMock
        ):
            await recorder._spawn_ffmpeg("rtsp://example/stream")

        cmd = captured["cmd"]
        self.assertIn("-map", cmd)
        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertIn("aac", cmd)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertNotIn("-an", cmd)


class CapacityTests(unittest.TestCase):
    def tearDown(self):
        ACTIVE_RECORDINGS.clear()

    def test_default_capacity_is_unlimited(self):
        with patch("app.services.recording_config.RECORDING_MAX_CONCURRENT", 0):
            info = get_recording_capacity_info()
        self.assertTrue(info["unlimited"])
        self.assertFalse(info["software_limit_enforced"])
        self.assertEqual(info["max_concurrent_recordings"], 0)

    def test_no_hardcoded_low_ceiling_in_default_config(self):
        from app.services import recording_config as rc

        # Default must not be a small artificial RDSO-under capacity (e.g. 1–8).
        self.assertEqual(rc.RECORDING_MAX_CONCURRENT, 0)


class SimultaneousPlaybackExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_while_active_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "index.m3u8").write_text(
                "#EXTM3U\n#EXTINF:2.0,\nseg_00001.ts\n", encoding="utf-8"
            )
            (session_dir / "seg_00001.ts").write_bytes(b"\x47" * 1880)
            mock_recorder = MagicMock(is_recording=True)

            async def _resolve(_cam, _sess, filename):
                return session_dir / filename

            with patch(
                "app.services.recording_media.resolve_recording_file",
                new_callable=AsyncMock,
                side_effect=_resolve,
            ), patch.dict(
                ACTIVE_RECORDINGS,
                {"cam1": {"session_id": "sess1", "recorder": mock_recorder}},
                clear=False,
            ):
                resp = await build_recording_media_response("cam1", "sess1", "index.m3u8")
                self.assertNotIn("#EXT-X-ENDLIST", resp.text or "")
                seg = await build_recording_media_response("cam1", "sess1", "seg_00001.ts")
                self.assertIsNotNone(getattr(seg, "_path", None))

    async def test_duplicate_recorder_prevented(self):
        ACTIVE_RECORDINGS.clear()
        ACTIVE_RECORDINGS["cam1"] = {
            "recorder": MagicMock(is_recording=True),
            "session_id": "sess-existing",
        }
        with patch(
            "app.services.recording_config.is_recording_engine_enabled", return_value=True
        ), patch(
            "app.services.video_recording.get_recording_session",
            new_callable=AsyncMock,
            return_value={"id": "sess-existing"},
        ):
            out = await start_camera_recording("cam1")
        self.assertEqual(out["id"], "sess-existing")


class TimeSyncTests(unittest.TestCase):
    def tearDown(self):
        clear_app_timezone_cache()

    def test_system_time_status_declares_os_ntp_responsibility(self):
        with patch(
            "app.services.os_time_sync.probe_os_time_sync",
            return_value={
                "service": "w32time",
                "service_running": True,
                "synchronized": True,
                "sync_state": "synchronized",
                "ntp_servers": [],
                "site_time_source": {"capable": True, "active": False, "guidance": "Windows Time"},
            },
        ):
            status = get_system_time_status()
        self.assertIn("utc_now", status)
        self.assertIn("app_timezone", status)
        self.assertFalse(status["ntp"]["in_app_ntp_server"])
        self.assertEqual(status["ntp"]["sync_responsibility"], "operating_system")
        self.assertEqual(status["sync_state"], "synchronized")
        self.assertIn("Windows Time", status["ntp"]["guidance"] or "")


if __name__ == "__main__":
    unittest.main()
