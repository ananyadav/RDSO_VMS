import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.live_latency import (
    FRONTEND_TELEMETRY,
    FrontendTelemetryStore,
    build_stream_latency,
    ingest_telemetry_payload,
    is_rtsp_connected_stderr,
    mark_first_segment_created,
    mark_playlist_created,
    mark_playlist_ready,
    mark_rtsp_connected,
    parse_playlist_meta,
)
from app.services.live_stream_registry import StreamRecord


class TestLiveLatencyHelpers(unittest.TestCase):
    def test_rtsp_stderr_detection(self):
        self.assertTrue(
            is_rtsp_connected_stderr("Opening 'rtsp://192.168.1.1/stream' for reading")
        )
        self.assertFalse(is_rtsp_connected_stderr("some other log line"))

    def test_parse_playlist_meta(self):
        playlist = Path(__file__).parent / "_tmp_playlist.m3u8"
        playlist.write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
            "#EXTINF:1.000,\nseg00001.ts\n#EXTINF:1.000,\nseg00002.ts\n",
            encoding="utf-8",
        )
        try:
            meta = parse_playlist_meta(
                playlist,
                segment_seconds_configured=1.0,
                list_size_configured=6,
            )
            self.assertEqual(meta["hlsPlaylistSegmentCount"], 2)
            self.assertEqual(meta["hlsSegmentDurationSec"], 1.0)
            self.assertEqual(meta["hlsListSizeConfigured"], 6)
        finally:
            playlist.unlink(missing_ok=True)

    def test_backend_milestones(self):
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=Path("/tmp/live.m3u8"),
            started_at_wall=1000.0,
        )
        mark_rtsp_connected(record)
        self.assertAlmostEqual(record.rtsp_connected_wall, time.time(), delta=2)
        mark_playlist_created(record)
        mark_first_segment_created(record)
        with patch("app.services.live_latency.time") as mock_time:
            mock_time.time.return_value = 1002.5
            mock_time.monotonic.return_value = 50.0
            record.started_at = 48.0
            ms = mark_playlist_ready(record)
        self.assertEqual(ms, 2000)
        self.assertEqual(record.startup_ms, 2000)

    def test_build_stream_latency(self):
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=Path("/nonexistent/live.m3u8"),
            started_at_wall=1000.0,
            rtsp_connected_wall=1000.5,
            playlist_created_wall=1001.0,
            first_segment_created_wall=1001.2,
            playlist_ready_wall=1001.5,
            startup_ms=1500,
        )
        lat = build_stream_latency(
            record,
            record.playlist_path,
            profile="grid",
            segment_seconds_configured=1.0,
            list_size_configured=6,
        )
        self.assertEqual(lat["backendStartupMs"], 1500)
        self.assertEqual(lat["firstSegmentMs"], 1200)
        self.assertEqual(lat["profile"], "grid")

    def test_frontend_telemetry_ingest(self):
        store = FrontendTelemetryStore()
        with patch("app.services.live_latency.FRONTEND_TELEMETRY", store):
            result = ingest_telemetry_payload(
                {
                    "streamId": "cam1",
                    "profile": "grid",
                    "acquireWall": 2000.0,
                    "manifestLoadedWall": 2001.0,
                    "videoPlayingWall": 2002.5,
                    "liveEdgeDelaySec": 3.2,
                    "bufferLengthSec": 8.0,
                }
            )
        self.assertTrue(result["ok"])
        snap = store.get("cam1")
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.startup_latency_ms(), 2500)
        self.assertAlmostEqual(snap.live_edge_delay_sec or 0, 3.2)


if __name__ == "__main__":
    unittest.main()
