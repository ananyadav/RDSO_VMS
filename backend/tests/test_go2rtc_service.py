import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.go2rtc_service import (
    _rtsp_with_tcp,
    _producer_url_matches,
    _src_for_go2rtc_api,
    build_all_streams_config,
    _base_yaml,
    stream_name,
    get_live_config,
)


class TestGo2RtcService(unittest.TestCase):
    def test_rtsp_tcp_suffix(self):
        url = "rtsp://u:p@1.2.3.4:554/Streaming/Channels/102"
        out = _rtsp_with_tcp(url)
        self.assertIn("rtsp_transport=tcp", out)

    def test_stream_name(self):
        self.assertEqual(stream_name("ip_192_168_41_50", "sub"), "ip_192_168_41_50_sub")
        self.assertEqual(stream_name("ip_192_168_41_50", "main"), "ip_192_168_41_50_main")

    def test_base_yaml_has_streams(self):
        y = _base_yaml(
            {"Cam18_sub": "rtsp://x", "Cam18_main": "rtsp://y"},
            api_port=1984,
            rtsp_port=8554,
            webrtc_port=8555,
        )
        self.assertEqual(y["streams"]["Cam18_sub"], "rtsp://x")
        self.assertIn("webrtc", y)
        self.assertIn("api", y)

    def test_live_config(self):
        cfg = get_live_config()
        self.assertIn(cfg["provider"], ("go2rtc", "hls"))
        self.assertIn("go2rtcEnabled", cfg)

    def test_producer_url_matches_primary_only(self):
        existing = {
            "producers": [
                {"url": "rtsp://cam/h264#rtsp_transport=tcp&timeout=20"},
            ]
        }
        desired = [
            "rtsp://cam/rtsp/streaming?channel=01&subtype=0#rtsp_transport=tcp&timeout=20",
            "rtsp://cam/h264#rtsp_transport=tcp&timeout=20",
        ]
        # Old primary equals a fallback — must NOT count as matched.
        self.assertFalse(_producer_url_matches(existing, desired))
        existing_ok = {
            "producers": [{"url": desired[0]}],
        }
        self.assertTrue(_producer_url_matches(existing_ok, desired))

    def test_producer_url_matches_list_url(self):
        existing = {
            "producers": [
                {
                    "url": [
                        "rtsp://cam/a#rtsp_transport=tcp&timeout=20",
                        "rtsp://cam/b#rtsp_transport=tcp&timeout=20",
                    ]
                }
            ]
        }
        desired = [
            "rtsp://cam/a#rtsp_transport=tcp&timeout=20",
            "rtsp://cam/b#rtsp_transport=tcp&timeout=20",
        ]
        self.assertTrue(_producer_url_matches(existing, desired))
        self.assertFalse(
            _producer_url_matches(
                existing,
                ["rtsp://cam/other#rtsp_transport=tcp&timeout=20"],
            )
        )

    def test_src_for_go2rtc_api_leaves_inner_query_intact(self):
        url = (
            "rtsp://u:p@1.2.3.4:554/rtsp/streaming?channel=01&subtype=0"
            "#rtsp_transport=tcp&timeout=20"
        )
        out = _src_for_go2rtc_api(url)
        self.assertIn("channel=01&subtype=0", out)
        self.assertIn("#rtsp_transport=tcp&timeout=20", out)


class TestBuildAllStreamsConfig(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.go2rtc_service.camera_collection")
    async def test_no_cameras(self, mock_coll):
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_coll.find.return_value = mock_cursor
        result = await build_all_streams_config()
        self.assertFalse(result["ok"])

    @patch("app.services.rtsp_utils.stream_source_urls")
    @patch("app.services.go2rtc_service.camera_collection")
    async def test_all_camera_streams(self, mock_coll, mock_sources):
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(
            return_value=[{
                "_id": "mongo18",
                "name": "Cam18",
                "camera_uid": "ip_10_0_0_18",
                "ip_address": "10.0.0.18",
            }]
        )
        mock_coll.find.return_value = mock_cursor

        def _sources(cam, *, main=False):
            if main:
                return ["rtsp://a@10.0.0.1:554/Streaming/Channels/101"]
            return ["rtsp://a@10.0.0.1:554/Streaming/Channels/102"]

        mock_sources.side_effect = _sources
        result = await build_all_streams_config()
        self.assertTrue(result["ok"])
        self.assertEqual(result["cameraCount"], 1)
        self.assertIn("ip_10_0_0_18_sub", result["streams"])
        self.assertIn("ip_10_0_0_18_main", result["streams"])
        self.assertIn("rtsp_transport=tcp", result["streams"]["ip_10_0_0_18_sub"])


if __name__ == "__main__":
    unittest.main()
