"""Tests for recording stream configuration."""

import importlib
import os
import unittest
from unittest.mock import patch


class RecordingConfigTests(unittest.TestCase):
    def _reload_rc(self):
        import app.services.recording_config as rc

        importlib.reload(rc)
        return rc

    def test_default_recording_stream_is_main(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECORDING_STREAM", None)
            rc = self._reload_rc()
            self.assertEqual(rc.RECORDING_STREAM, "main")
            info = rc.get_recording_stream_info()
            self.assertEqual(info["channel"], "101")
            self.assertEqual(info["quality_label"], "Main Stream / Evidence Quality")
            self.assertFalse(info["substream_warning"])

    def test_substream_warning_when_configured_sub(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "sub"}):
            rc = self._reload_rc()
            info = rc.get_recording_stream_info()
            self.assertEqual(info["channel"], "102")
            self.assertTrue(info["substream_warning"])

    def test_resolve_recording_url_uses_main(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "main", "RECORDING_VIA_GO2RTC": "false"}):
            rc = self._reload_rc()
            cam = {
                "recording_channel": "main",
                "main_rtsp_url": "rtsp://x/101",
                "sub_rtsp_url": "rtsp://x/102",
            }
            url, label = rc.resolve_recording_rtsp_url(cam, cam)
            self.assertEqual(url, "rtsp://x/101")
            self.assertEqual(label, "main/101")

    def test_per_camera_sub_overrides_global_main(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "main", "RECORDING_VIA_GO2RTC": "false"}):
            rc = self._reload_rc()
            cam = {
                "recording_channel": "sub",
                "main_rtsp_url": "rtsp://x/101",
                "sub_rtsp_url": "rtsp://x/102",
            }
            url, label = rc.resolve_recording_rtsp_url(cam, cam)
            self.assertEqual(url, "rtsp://x/102")
            self.assertEqual(label, "sub/102")

    def test_missing_recording_channel_uses_global_fallback(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "sub", "RECORDING_VIA_GO2RTC": "false"}):
            rc = self._reload_rc()
            cam = {"main_rtsp_url": "rtsp://x/101", "sub_rtsp_url": "rtsp://x/102"}
            self.assertEqual(rc.resolve_recording_stream_choice(cam), "sub")
            url, _label = rc.resolve_recording_rtsp_url(cam, cam)
            self.assertEqual(url, "rtsp://x/102")

    def test_legacy_channel_number_maps_to_main_or_sub(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "sub", "RECORDING_VIA_GO2RTC": "false"}):
            rc = self._reload_rc()
            cam = {
                "recording_channel": "101",
                "main_channel": "101",
                "sub_channel": "102",
                "main_rtsp_url": "rtsp://x/101",
                "sub_rtsp_url": "rtsp://x/102",
            }
            self.assertEqual(rc.resolve_recording_stream_choice(cam), "main")
            url, _label = rc.resolve_recording_rtsp_url(cam, cam)
            self.assertEqual(url, "rtsp://x/101")

    def test_camera_a_setting_does_not_change_camera_b_resolution(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "main", "RECORDING_VIA_GO2RTC": "false"}):
            rc = self._reload_rc()
            cam_a = {
                "recording_channel": "sub",
                "main_rtsp_url": "rtsp://a/101",
                "sub_rtsp_url": "rtsp://a/102",
            }
            cam_b = {
                "recording_channel": "main",
                "main_rtsp_url": "rtsp://b/101",
                "sub_rtsp_url": "rtsp://b/102",
            }
            url_a, _ = rc.resolve_recording_rtsp_url(cam_a, cam_a)
            url_b, _ = rc.resolve_recording_rtsp_url(cam_b, cam_b)
            self.assertEqual(url_a, "rtsp://a/102")
            self.assertEqual(url_b, "rtsp://b/101")


if __name__ == "__main__":
    unittest.main()
