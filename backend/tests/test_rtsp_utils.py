import unittest

from app.services.rtsp_utils import (
    build_rtsp_url,
    build_camera_rtsp_urls,
    effective_camera_rtsp_urls,
    mask_rtsp_url,
    rewrite_rtsp_credentials,
    rtsp_url_credentials_stale,
    sync_camera_rtsp_urls,
)


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

    def test_effective_custom_rtsp_urls(self):
        urls = effective_camera_rtsp_urls({
            "protocol": "CUSTOM",
            "username": "admin",
            "password": "newpass",
            "sub_rtsp_url": "rtsp://admin:oldpass@10.0.0.5:554/ch01/sub/av_stream",
            "main_rtsp_url": "rtsp://admin:oldpass@10.0.0.5:554/11",
        })
        self.assertIn("newpass", urls["sub_rtsp_url"])
        self.assertIn("newpass", urls["main_rtsp_url"])

    def test_rtsp_url_credentials_stale(self):
        cam = {
            "username": "admin",
            "password": "Rashmi@432",
            "sub_rtsp_url": "rtsp://admin:Corp%232024@192.168.46.12:554/Streaming/Channels/102",
        }
        self.assertTrue(rtsp_url_credentials_stale(cam))

    def test_sync_camera_rtsp_urls_hikvision(self):
        synced = sync_camera_rtsp_urls({
            "protocol": "HIKVISION",
            "ip_address": "192.168.46.12",
            "username": "admin",
            "password": "Rashmi@432",
            "sub_rtsp_url": "rtsp://admin:Corp%232024@192.168.46.12:554/Streaming/Channels/102",
        })
        self.assertIn("Rashmi%40432", synced["sub_rtsp_url"])
        self.assertNotIn("Corp%232024", synced["sub_rtsp_url"])

    def test_rewrite_rtsp_credentials(self):
        out = rewrite_rtsp_credentials(
            "rtsp://admin:old@10.0.0.5:554/path",
            "admin",
            "new@pass",
        )
        self.assertIn("new%40pass", out)
        self.assertIn("10.0.0.5:554/path", out)

    def test_mask_rtsp_url(self):
        masked = mask_rtsp_url("rtsp://admin:secret@10.0.0.5:554/path")
        self.assertNotIn("secret", masked)
        self.assertIn("10.0.0.5", masked)


if __name__ == "__main__":
    unittest.main()
