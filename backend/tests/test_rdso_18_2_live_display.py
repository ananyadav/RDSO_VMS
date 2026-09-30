"""RDSO 18.2.5–18.2.7 — Live View display layout capability (software)."""

from __future__ import annotations

import unittest

from app.services.go2rtc_service import get_live_config


class LiveDisplayCapability(unittest.TestCase):
    def test_live_config_layouts_include_16_and_site(self):
        cfg = get_live_config()
        disp = cfg.get("live_display") or {}
        self.assertTrue(disp.get("rdso_18_2_5"))
        self.assertTrue(disp.get("rdso_18_2_6"))
        self.assertTrue(disp.get("rdso_18_2_7"))
        layouts = {row["id"]: row for row in disp.get("layouts") or []}
        self.assertEqual(layouts["1x1"]["tiles"], 1)
        self.assertEqual(layouts["2x2"]["tiles"], 4)
        self.assertEqual(layouts["4x4"]["tiles"], 16)
        self.assertEqual(layouts["6x6"]["tiles"], 36)
        self.assertEqual(disp.get("min_simultaneous_tiles"), 16)
        self.assertEqual(disp.get("grid_stream"), "sub")
        self.assertEqual(disp.get("fullscreen_stream"), "main")
        self.assertEqual(disp.get("software_fps_capability"), 25)
        self.assertFalse(disp.get("software_fps_throttle_below_25"))
        self.assertTrue(disp.get("workstation_acceptance_required"))
        self.assertTrue(disp.get("rdso_18_2_24"))
        self.assertTrue(disp.get("rdso_18_2_25"))
        self.assertTrue(disp.get("rdso_18_2_26"))
        self.assertTrue(disp.get("resolution_responsive"))
        self.assertFalse(disp.get("hardcoded_physical_display_resolution"))
        self.assertFalse(disp.get("physical_55_inch_tested"))
        self.assertTrue(disp.get("client_pc_display_control"))
        self.assertEqual(disp.get("logical_monitors"), 8)
        self.assertTrue(disp.get("independent_monitor_layout_and_cameras"))

    def test_stream_profile_allows_25_fps(self):
        from app.services.stream_profile_service import MAX_FPS, MIN_FPS

        self.assertEqual(MIN_FPS, 1)
        self.assertGreaterEqual(MAX_FPS, 25)


if __name__ == "__main__":
    unittest.main()
