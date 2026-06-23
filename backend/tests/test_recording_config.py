"""Tests for recording stream configuration."""

import importlib
import os
import unittest
from unittest.mock import patch


class RecordingConfigTests(unittest.TestCase):
    def test_default_recording_stream_is_main(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECORDING_STREAM", None)
            import app.services.recording_config as rc

            importlib.reload(rc)
            self.assertEqual(rc.RECORDING_STREAM, "main")
            info = rc.get_recording_stream_info()
            self.assertEqual(info["channel"], "101")
            self.assertEqual(info["quality_label"], "Main Stream / Evidence Quality")
            self.assertFalse(info["substream_warning"])

    def test_substream_warning_when_configured_sub(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "sub"}):
            import app.services.recording_config as rc

            importlib.reload(rc)
            info = rc.get_recording_stream_info()
            self.assertEqual(info["channel"], "102")
            self.assertTrue(info["substream_warning"])

    def test_resolve_recording_url_uses_main(self):
        with patch.dict(os.environ, {"RECORDING_STREAM": "main"}):
            import app.services.recording_config as rc

            importlib.reload(rc)
            cam = {"main_rtsp_url": "rtsp://x/101", "sub_rtsp_url": "rtsp://x/102"}
            url, label = rc.resolve_recording_rtsp_url(cam, cam)
            self.assertEqual(url, "rtsp://x/101")
            self.assertEqual(label, "main/101")


if __name__ == "__main__":
    unittest.main()
