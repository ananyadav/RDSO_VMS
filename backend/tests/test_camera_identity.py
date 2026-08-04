import unittest

from app.services.camera_uid import (
    apply_default_camera_names,
    camera_display_name,
    ip_from_camera_uid,
    make_camera_uid,
)


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

    def test_apply_default_camera_names_new_camera(self):
        doc = apply_default_camera_names({"ip_address": "192.168.1.10"})
        self.assertEqual(doc["name"], "192.168.1.10")
        self.assertEqual(doc["display_name"], "192.168.1.10")

    def test_apply_default_camera_names_keeps_custom(self):
        doc = apply_default_camera_names(
            {"ip_address": "192.168.1.10", "name": "Manhole Cam 1", "display_name": "Manhole Cam 1"},
            existing={"ip_address": "192.168.1.10", "name": "192.168.1.10"},
        )
        self.assertEqual(doc["name"], "Manhole Cam 1")
        self.assertEqual(doc["display_name"], "Manhole Cam 1")

    def test_apply_default_camera_names_ip_change_updates_placeholder(self):
        doc = apply_default_camera_names(
            {"ip_address": "192.168.1.11", "name": "192.168.1.10"},
            existing={"ip_address": "192.168.1.10", "name": "192.168.1.10"},
        )
        self.assertEqual(doc["name"], "192.168.1.11")


if __name__ == "__main__":
    unittest.main()
