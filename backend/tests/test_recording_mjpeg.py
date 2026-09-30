"""RDSO 18.3.1 MJPEG recording + H.264/H.265 regression tests."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ffmpeg_util import ffmpeg_bin
from app.services.recording_codec import (
    VIDEO_MODE_COPY,
    VIDEO_MODE_ENCODE_H264,
    classify_recording_video_mode,
    video_encode_args_for_mode,
)
from app.services.recording_config import get_recording_stream_info
from app.services.video_recording import VideoRecorder


class ClassifyCodecTests(unittest.TestCase):
    def test_h264_hevc_copy(self):
        self.assertEqual(classify_recording_video_mode("h264"), VIDEO_MODE_COPY)
        self.assertEqual(classify_recording_video_mode("hevc"), VIDEO_MODE_COPY)
        self.assertEqual(classify_recording_video_mode("h265"), VIDEO_MODE_COPY)

    def test_mjpeg_encodes(self):
        self.assertEqual(classify_recording_video_mode("mjpeg"), VIDEO_MODE_ENCODE_H264)
        self.assertEqual(classify_recording_video_mode("MJPEG"), VIDEO_MODE_ENCODE_H264)

    def test_encode_args(self):
        self.assertEqual(video_encode_args_for_mode(VIDEO_MODE_COPY), ["-c:v", "copy"])
        args = video_encode_args_for_mode(VIDEO_MODE_ENCODE_H264)
        self.assertIn("libx264", args)
        self.assertNotIn("copy", args)


class StreamInfoMjpegTests(unittest.TestCase):
    def test_mjpeg_listed(self):
        info = get_recording_stream_info()
        self.assertIn("MJPEG", info["video_codecs_supported"])
        self.assertIn("H.265", info["video_codecs_supported"])


class SpawnModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_h265_stays_copy_with_audio(self):
        recorder = VideoRecorder("507f1f77bcf86cd799439011", "sess_hevc")
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
            return_value={"ok": True, "codec_name": "hevc", "video_mode": VIDEO_MODE_COPY},
        ), patch("app.services.recording_config.RECORDING_AUDIO_ENABLED", True), patch(
            "asyncio.create_subprocess_exec", side_effect=fake_exec
        ), patch.object(recorder, "_read_ffmpeg_stderr", new_callable=AsyncMock), patch(
            "app.services.video_recording.update_recording_session", new_callable=AsyncMock
        ):
            await recorder._spawn_ffmpeg("rtsp://example/hevc")

        cmd = captured["cmd"]
        # -c:v copy appears; libx264 must not
        cv = cmd.index("-c:v")
        self.assertEqual(cmd[cv + 1], "copy")
        self.assertNotIn("libx264", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertIn("aac", cmd)
        self.assertEqual(recorder._video_mode, VIDEO_MODE_COPY)

    async def test_mjpeg_uses_libx264(self):
        recorder = VideoRecorder("507f1f77bcf86cd799439011", "sess_mjpeg")
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
            return_value={"ok": True, "codec_name": "mjpeg", "video_mode": VIDEO_MODE_ENCODE_H264},
        ), patch("app.services.recording_config.RECORDING_AUDIO_ENABLED", True), patch(
            "asyncio.create_subprocess_exec", side_effect=fake_exec
        ), patch.object(recorder, "_read_ffmpeg_stderr", new_callable=AsyncMock), patch(
            "app.services.video_recording.update_recording_session", new_callable=AsyncMock
        ):
            await recorder._spawn_ffmpeg("rtsp://example/mjpeg")

        cmd = captured["cmd"]
        self.assertIn("libx264", cmd)
        self.assertNotEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertIn("0:a:0?", cmd)
        self.assertEqual(recorder._video_mode, VIDEO_MODE_ENCODE_H264)
        self.assertEqual(recorder._source_codec, "mjpeg")


@unittest.skipUnless(shutil.which("ffmpeg") or Path(ffmpeg_bin()).exists(), "ffmpeg required")
class SyntheticMjpegArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_mjpeg_produces_playable_hls(self):
        """Generate local MJPEG, archive via VideoRecorder path into HLS (H.264)."""
        ffmpeg = ffmpeg_bin()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src_mjpeg.avi"
            # Short MJPEG AVI (widely probeable).
            gen = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=320x240:rate=5:duration=3",
                "-c:v",
                "mjpeg",
                "-y",
                str(src),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await gen.communicate()
            self.assertEqual(gen.returncode, 0, err.decode("utf-8", errors="ignore"))
            self.assertTrue(src.is_file())

            from app.services.recording_codec import probe_recording_video_codec

            probe = await probe_recording_video_codec(str(src))
            self.assertTrue(probe["ok"], probe)
            self.assertEqual(probe["video_mode"], VIDEO_MODE_ENCODE_H264)

            recorder = VideoRecorder("507f1f77bcf86cd799439011", "sess_syn_mjpeg")
            recorder.session_dir = tmp_path / "out_session"
            recorder.session_dir.mkdir(parents=True, exist_ok=True)
            recorder.is_recording = True

            with patch("app.services.recording_config.RECORDING_AUDIO_ENABLED", False), patch(
                "app.services.video_recording.RECORDING_SEGMENT_SECONDS", "1"
            ), patch(
                "app.services.video_recording.update_recording_session", new_callable=AsyncMock
            ):
                await recorder._spawn_ffmpeg(str(src))

            # Wait for playlist + segment
            playlist = recorder.session_dir / "index.m3u8"
            deadline = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < deadline:
                if playlist.is_file() and any(recorder.session_dir.glob("seg_*.ts")):
                    break
                await asyncio.sleep(0.5)

            self.assertTrue(playlist.is_file(), list(recorder.session_dir.iterdir()))
            text = playlist.read_text(encoding="utf-8", errors="ignore")
            self.assertIn("#EXTM3U", text)
            self.assertTrue(any(recorder.session_dir.glob("seg_*.ts")))
            self.assertEqual(recorder._video_mode, VIDEO_MODE_ENCODE_H264)

            # Stop ffmpeg
            recorder.is_recording = False
            if recorder.recording_process and recorder.recording_process.returncode is None:
                recorder.recording_process.terminate()
                try:
                    await asyncio.wait_for(recorder.recording_process.wait(), timeout=5)
                except Exception:
                    recorder.recording_process.kill()


if __name__ == "__main__":
    unittest.main()
