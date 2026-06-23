import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.live_stream_debug import (
    _api_payload,
    channel_from_label,
    channel_from_url,
    clear_debug_store,
    get_fullscreen_debug,
    is_fullscreen_stream_id,
    probe_rtsp_sync,
    schedule_fullscreen_verification,
    verify_fullscreen_stream,
)


class TestLiveStreamDebugHelpers(unittest.TestCase):
    def test_is_fullscreen_stream_id(self):
        self.assertTrue(is_fullscreen_stream_id("abc__fullscreen"))
        self.assertFalse(is_fullscreen_stream_id("abc"))

    def test_channel_from_label(self):
        self.assertEqual(channel_from_label("main/101"), "101")
        self.assertEqual(channel_from_label("sub/102 (no main)"), "102")
        self.assertEqual(channel_from_label("preview/103"), "103")

    def test_channel_from_url(self):
        self.assertEqual(
            channel_from_url("rtsp://1.2.3.4:554/Streaming/Channels/101"),
            "101",
        )

    def test_api_payload(self):
        payload = _api_payload(
            {
                "channel": "101",
                "resolution": "3840x2160",
                "codec": "hevc",
                "fps": 25.0,
            }
        )
        self.assertEqual(payload["channel"], "101")
        self.assertEqual(payload["resolution"], "3840x2160")
        self.assertEqual(payload["codec"], "hevc")
        self.assertEqual(payload["fps"], 25.0)


class TestLiveStreamDebugAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clear_debug_store()

    async def asyncTearDown(self):
        clear_debug_store()

    @patch("app.services.live_stream_debug.asyncio.sleep", new_callable=AsyncMock)
    @patch(
        "app.services.live_stream_debug.probe_rtsp",
        new_callable=AsyncMock,
    )
    @patch(
        "app.services.live_stream_debug._pick_live_urls",
        return_value=("rtsp://x/101", "main/101"),
    )
    @patch(
        "app.services.live_stream_debug._get_camera_doc",
        new_callable=AsyncMock,
    )
    @patch("app.services.live_stream_debug.REGISTRY")
    async def test_verify_stores_probe(
        self, mock_registry, mock_get_cam, mock_pick, mock_probe, _mock_sleep
    ):
        mock_get_cam.return_value = {"name": "Cam1"}
        mock_registry.get.return_value = None
        mock_probe.return_value = {
            "codec": "hevc",
            "resolution": "3840x2160",
            "fps": 25.0,
            "bitrate_kbps": 4096,
        }

        await verify_fullscreen_stream("cam1__fullscreen")

        data = await get_fullscreen_debug("cam1")
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["channel"], "101")
        self.assertEqual(data["resolution"], "3840x2160")
        self.assertEqual(data["codec"], "hevc")
        self.assertEqual(data["fps"], 25.0)

    @patch("app.services.live_stream_debug.asyncio.sleep", new_callable=AsyncMock)
    def test_skip_probe_when_cache_fresh(self, _mock_sleep):
        from app.services import live_stream_debug as dbg

        dbg._DEBUG_STORE["cam1"] = {"channel": "101"}
        dbg._last_probe_at["cam1"] = __import__("time").monotonic()
        reason = dbg._should_skip_probe("cam1", "cam1__fullscreen")
        self.assertEqual(reason, "probe cache fresh (60s)")

    @patch("app.services.live_stream_debug.asyncio.create_task")
    def test_schedule_only_for_fullscreen(self, mock_create_task):
        schedule_fullscreen_verification("cam1")
        mock_create_task.assert_not_called()
        schedule_fullscreen_verification("cam1__fullscreen")
        mock_create_task.assert_called_once()


class TestProbeRtspSync(unittest.TestCase):
    @patch("app.services.live_stream_debug.subprocess.run")
    @patch("app.services.live_stream_debug.ffprobe_bin", return_value="ffprobe")
    def test_probe_parses_json(self, _mock_bin, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_name":"hevc","width":1920,"height":1080,'
            '"r_frame_rate":"25/1","bit_rate":"4000000"}]}',
            stderr="",
        )
        result = probe_rtsp_sync("rtsp://test/101")
        self.assertEqual(result["codec"], "hevc")
        self.assertEqual(result["resolution"], "1920x1080")
        self.assertEqual(result["fps"], 25.0)
        self.assertEqual(result["bitrate_kbps"], 4000)


if __name__ == "__main__":
    unittest.main()
