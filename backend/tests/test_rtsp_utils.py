import unittest

from app.services.rtsp_utils import build_rtsp_url, build_camera_rtsp_urls, mask_rtsp_url


class TestRtspUtils(unittest.TestCase):
    def test_hikvision_sub_and_main(self):
        sub = build_rtsp_url(
            ip_address="192.168.1.10",
            port=554,
            username="admin",
            password="pass",
            model="Hikvision",
            channel="102",
            main=False,
        )
        main = build_rtsp_url(
            ip_address="192.168.1.10",
            port=554,
            username="admin",
            password="pass",
            model="Hikvision",
            channel="101",
            main=True,
        )
        self.assertIn("/Streaming/Channels/102", sub)
        self.assertIn("/Streaming/Channels/101", main)

    def test_build_camera_rtsp_urls(self):
        urls = build_camera_rtsp_urls({
            "ip_address": "10.0.0.5",
            "port": 554,
            "username": "admin",
            "password": "secret",
            "model": "hikvision",
            "recording_channel": "102",
        })
        self.assertIn("main_rtsp_url", urls)
        self.assertIn("sub_rtsp_url", urls)

    def test_mask_rtsp_url(self):
        masked = mask_rtsp_url("rtsp://admin:secret@10.0.0.5:554/path")
        self.assertNotIn("secret", masked)
        self.assertIn("10.0.0.5", masked)


if __name__ == "__main__":
    unittest.main()
