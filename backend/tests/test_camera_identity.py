import unittest

from app.services.camera_uid import camera_display_name, ip_from_camera_uid, make_camera_uid


class TestCameraIdentity(unittest.TestCase):
    def test_make_camera_uid(self):
        self.assertEqual(make_camera_uid("192.168.41.50"), "ip_192_168_41_50")
        self.assertIsNone(make_camera_uid(""))

    def test_ip_from_camera_uid(self):
        self.assertEqual(ip_from_camera_uid("ip_192_168_41_50"), "192.168.41.50")
        self.assertIsNone(ip_from_camera_uid("Cam18"))

    def test_display_name(self):
        cam = {"name": "Cam18", "floor_group": "6th Floor"}
        self.assertEqual(camera_display_name(cam), "6th Floor - Cam18")


if __name__ == "__main__":
    unittest.main()
