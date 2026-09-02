"""RDSO 18.1.5 — architectural tests for simultaneous record + live + playback."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.recording_media import build_recording_media_response
from app.services.video_recording import ACTIVE_RECORDINGS, start_camera_recording


class TestRdso1815Simultaneous(unittest.IsolatedAsyncioTestCase):
    async def test_playback_reads_while_session_active(self):
        """Playback layer serves playlist/segments without file locks during active recording."""
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            playlist = session_dir / "index.m3u8"
            seg = session_dir / "seg_00001.ts"
            playlist.write_text("#EXTM3U\n#EXTINF:10.0,\nseg_00001.ts\n", encoding="utf-8")
            seg.write_bytes(b"\x47" * 1880)

            mock_recorder = MagicMock()
            mock_recorder.is_recording = True

            with patch(
                "app.services.recording_media.resolve_recording_file",
                new_callable=AsyncMock,
            ) as resolve_file:
                async def _resolve(_cam, _sess, filename):
                    return session_dir / filename

                resolve_file.side_effect = _resolve

                with patch.dict(
                    ACTIVE_RECORDINGS,
                    {
                        "cam1": {
                            "session_id": "sess1",
                            "recorder": mock_recorder,
                        }
                    },
                    clear=False,
                ):
                    resp = await build_recording_media_response("cam1", "sess1", "index.m3u8")
                    self.assertIn("#EXTM3U", resp.text or "")
                    self.assertNotIn("#EXT-X-ENDLIST", resp.text or "")

                    seg_resp = await build_recording_media_response("cam1", "sess1", "seg_00001.ts")
                    self.assertIsNotNone(getattr(seg_resp, "_path", None))

    async def test_start_camera_recording_rejects_duplicate_ffmpeg(self):
        """Per-camera lock prevents a second FFmpeg for the same camera."""
        existing_session = {"id": "sess-existing", "camera_id": "cam1"}
        mock_recorder = MagicMock()
        mock_recorder.is_recording = True

        with patch.dict(
            ACTIVE_RECORDINGS,
            {"cam1": {"recorder": mock_recorder, "session_id": "sess-existing"}},
            clear=True,
        ), patch(
            "app.services.video_recording.get_recording_session",
            new_callable=AsyncMock,
            return_value=existing_session,
        ), patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ):
            session = await start_camera_recording("cam1")
            self.assertEqual(session["id"], "sess-existing")
