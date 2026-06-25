import unittest

from app.services.camera_sync import (
    changed_fields,
    finalize_camera_fields,
    stream_config_changed,
)


class TestCameraSync(unittest.TestCase):
    def test_changed_fields(self):
        existing = {"password": "old", "username": "admin", "port": 554}
        updated = {"password": "new", "port": 554}
        self.assertEqual(changed_fields(existing, updated), {"password"})

    def test_stream_config_changed_password(self):
        existing = {"password": "old", "sub_rtsp_url": "rtsp://admin:old@1.2.3.4:554/x"}
        fields = {"password": "new"}
        self.assertTrue(stream_config_changed(existing, fields))

    def test_stream_config_unchanged_location(self):
        existing = {"password": "x", "building": "A", "sub_rtsp_url": "rtsp://admin:x@1.2.3.4:554/x"}
        fields = {"building": "B"}
        self.assertFalse(stream_config_changed(existing, fields))

    def test_finalize_rebuilds_rtsp_on_password_change(self):
        existing = {
            "protocol": "HIKVISION",
            "ip_address": "192.168.1.10",
            "username": "admin",
            "password": "Corp#2024",
            "sub_rtsp_url": "rtsp://admin:Corp%232024@192.168.1.10:554/Streaming/Channels/102",
        }
        fields = {"password": "Rashmi@432"}
        out = finalize_camera_fields(existing, fields)
        self.assertIn("Rashmi%40432", out["sub_rtsp_url"])
        self.assertNotIn("Corp%232024", out["sub_rtsp_url"])


if __name__ == "__main__":
    unittest.main()
