import unittest
from unittest.mock import patch

from app.services.video_live_hls import (
    FULLSCREEN_SUFFIX,
    _pick_fullscreen_urls,
    _pick_grid_urls,
    _pick_live_urls,
)


class TestLiveStreamUrlPicking(unittest.TestCase):
    def setUp(self):
        self.cam = {
            "sub_rtsp_url": "rtsp://cam/sub102",
            "main_rtsp_url": "rtsp://cam/main101",
        }

    def test_grid_always_sub_102(self):
        url, label = _pick_grid_urls(self.cam)
        self.assertEqual(url, "rtsp://cam/sub102")
        self.assertEqual(label, "sub/102")

    def test_grid_stream_id_ignores_env_preview(self):
        with patch("app.services.video_live_hls.LIVE_STREAM", "preview"):
            url, label = _pick_live_urls(
                self.cam,
                stream_id="abc123",
                force_sub=False,
            )
        self.assertEqual(url, "rtsp://cam/sub102")
        self.assertEqual(label, "sub/102")

    @patch("app.services.video_live_hls.FULLSCREEN_STREAM", "main")
    def test_fullscreen_default_main_101(self):
        url, label = _pick_fullscreen_urls(self.cam)
        self.assertEqual(url, "rtsp://cam/main101")
        self.assertEqual(label, "main/101")

    @patch("app.services.video_live_hls.FULLSCREEN_STREAM", "main")
    def test_fullscreen_fallback_sub_when_forced(self):
        url, label = _pick_fullscreen_urls(self.cam, force_sub=True)
        self.assertEqual(url, "rtsp://cam/sub102")
        self.assertEqual(label, "sub/102")

    @patch("app.services.video_live_hls.FULLSCREEN_STREAM", "preview")
    def test_fullscreen_legacy_preview_env_uses_main(self):
        url, label = _pick_fullscreen_urls(self.cam)
        self.assertEqual(url, "rtsp://cam/main101")
        self.assertEqual(label, "main/101")

    def test_fullscreen_stream_suffix(self):
        stream_id = f"camid{FULLSCREEN_SUFFIX}"
        with patch("app.services.video_live_hls.FULLSCREEN_STREAM", "main"):
            url, label = _pick_live_urls(self.cam, stream_id=stream_id)
        self.assertEqual(url, "rtsp://cam/main101")
        self.assertEqual(label, "main/101")


if __name__ == "__main__":
    unittest.main()
