"""Tests for go2rtc worker registry helpers."""

import unittest

from app.services.go2rtc_workers import (
    MAX_CAMERAS_PER_WORKER,
    needed_workers_for_camera_count,
    normalize_worker_id,
    worker_base_url,
    worker_config_path,
    worker_pm2_name,
    worker_ports,
)


class TestGo2RtcWorkers(unittest.TestCase):
    def test_normalize_worker_id(self):
        self.assertEqual(normalize_worker_id(2), 2)
        self.assertEqual(normalize_worker_id("2"), 2)
        self.assertEqual(normalize_worker_id("worker-1"), 1)
        self.assertEqual(normalize_worker_id("go2rtc-worker-3"), 3)
        self.assertIsNone(normalize_worker_id(""))
        self.assertIsNone(normalize_worker_id(None))

    def test_worker_ports_increment(self):
        self.assertEqual(worker_ports(1), (1984, 8554, 8555))
        self.assertEqual(worker_ports(2), (1985, 8556, 8557))
        self.assertEqual(worker_ports(3), (1986, 8558, 8559))

    def test_worker_paths_and_names(self):
        self.assertEqual(worker_pm2_name(2), "go2rtc-worker-2")
        self.assertTrue(str(worker_config_path(2)).replace("\\", "/").endswith("go2rtc/workers/2/go2rtc.yaml"))
        self.assertEqual(worker_base_url(2), "http://127.0.0.1:1985")

    def test_max_cameras_default(self):
        self.assertEqual(MAX_CAMERAS_PER_WORKER, 300)

    def test_camera_group_key_prefers_stored(self):
        from app.services.camera_locations import camera_group_key_for_document

        cam = {
            "site": "RML - 6",
            "building": "KIPL",
            "floor": "KIPL All",
            "camera_group": "rml_3_kipl_kipl_all",
        }
        self.assertEqual(camera_group_key_for_document(cam), "rml_3_kipl_kipl_all")

    def test_needed_workers_for_camera_count(self):
        self.assertEqual(needed_workers_for_camera_count(0), 0)
        self.assertEqual(needed_workers_for_camera_count(1), 1)
        self.assertEqual(needed_workers_for_camera_count(300), 1)
        self.assertEqual(needed_workers_for_camera_count(301), 2)
        self.assertEqual(needed_workers_for_camera_count(653), 3)
        self.assertEqual(needed_workers_for_camera_count(900), 3)
        self.assertEqual(needed_workers_for_camera_count(901), 4)

    def test_assign_worker_uses_needed_slot_not_max_plus_one(self):
        """At 901 cameras worker 4 should be used, not worker 5 when worker 4 exists inactive."""
        needed = needed_workers_for_camera_count(901)
        self.assertEqual(needed, 4)
        self.assertEqual((900 // MAX_CAMERAS_PER_WORKER) + 1, 4)

    def test_orphan_worker_outside_needed_range(self):
        needed = needed_workers_for_camera_count(650)
        self.assertEqual(needed, 3)
        valid = set(range(1, needed + 1))
        self.assertNotIn(4, valid)


if __name__ == "__main__":
    unittest.main()
