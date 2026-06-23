import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.live_stream_registry import REGISTRY, StreamRecord, StreamStatus
from app.services.video_live_hls import get_live_diagnostics


class TestLiveDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original = dict(REGISTRY._streams)
        REGISTRY._streams.clear()

    async def asyncTearDown(self):
        REGISTRY._streams.clear()
        REGISTRY._streams.update(self._original)

    @patch("app.services.video_live_hls.build_ffmpeg_diagnostics_extra")
    @patch("app.services.video_live_hls.is_playlist_ready", new_callable=AsyncMock)
    @patch("app.services.video_live_hls._camera_names", new_callable=AsyncMock)
    async def test_empty_diagnostics(self, mock_names, mock_ready, mock_extra):
        mock_names.return_value = {}
        mock_ready.return_value = False
        mock_extra.return_value = {
            "trackedFfmpegCount": 0,
            "orphanFfmpegCount": 0,
            "totalNvrFfmpegCount": 0,
            "cameraRtspSessions": [],
            "warnings": [],
            "orphans": [],
        }
        result = await get_live_diagnostics()
        self.assertEqual(result["activeStreamCount"], 0)
        self.assertEqual(result["ffmpegProcessCount"], 0)
        self.assertEqual(result["streams"], [])
        self.assertEqual(result["orphanFfmpegCount"], 0)

    @patch("app.services.video_live_hls.build_ffmpeg_diagnostics_extra")
    @patch("app.services.video_live_hls.is_playlist_ready", new_callable=AsyncMock)
    @patch("app.services.video_live_hls._camera_names", new_callable=AsyncMock)
    async def test_stream_row_fields(self, mock_names, mock_ready, mock_extra):
        mock_names.return_value = {"cam1": "Cam 1"}
        mock_ready.return_value = True
        mock_extra.return_value = {
            "trackedFfmpegCount": 1,
            "orphanFfmpegCount": 0,
            "totalNvrFfmpegCount": 1,
            "cameraRtspSessions": [],
            "warnings": [],
            "orphans": [],
        }

        proc = MagicMock()
        proc.pid = 4242
        proc.returncode = None

        record = StreamRecord(
            stream_id="cam1",
            playlist_path=Path("/tmp/nvr_live/cam1/live.m3u8"),
            proc=proc,
            status=StreamStatus.RUNNING,
            ref_count=2,
            started_at_wall=1_700_000_000.0,
            startup_ms=850,
            stream_label="sub/102",
        )
        REGISTRY._streams["cam1"] = record

        result = await get_live_diagnostics()
        self.assertEqual(result["activeStreamCount"], 1)
        self.assertEqual(result["ffmpegProcessCount"], 1)
        self.assertEqual(len(result["streams"]), 1)

        row = result["streams"][0]
        self.assertEqual(row["cameraName"], "Cam 1")
        self.assertEqual(row["streamId"], "cam1")
        self.assertEqual(row["profile"], "grid")
        self.assertEqual(row["ffmpegPid"], 4242)
        self.assertEqual(row["refCount"], 2)
        self.assertTrue(row["playlistReady"])
        self.assertEqual(row["startupMs"], 850)
        self.assertIsNotNone(row["startedAt"])
        self.assertIn("latency", row)
        self.assertEqual(row["latency"]["profile"], "grid")
        self.assertEqual(row["latency"]["backendStartupMs"], 850)


if __name__ == "__main__":
    unittest.main()
