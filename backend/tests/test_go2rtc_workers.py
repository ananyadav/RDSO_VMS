"""Tests for go2rtc worker registry helpers."""

import unittest

from app.services.go2rtc_workers import (
    MAX_CAMERAS_PER_WORKER,
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


if __name__ == "__main__":
    unittest.main()
