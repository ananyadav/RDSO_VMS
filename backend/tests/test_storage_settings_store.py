import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.storage_settings_store import (
    _repair_mixed_recordings_path,
    normalize_recordings_path,
)


class TestRecordingsPathNormalization(unittest.TestCase):
    def test_repair_posix_plus_windows_concatenation(self):
        corrupt = (
            "/home/vms/cctv_ananya/cctv_1/nvr-cctv/backend/"
            r"C:\Users\Ananya Yadav\Cursor Workspace\CCTV\Recordings"
        )
        with patch.object(os, "name", "nt"):
            repaired = _repair_mixed_recordings_path(corrupt)
        self.assertEqual(
            repaired,
            r"C:\Users\Ananya Yadav\Cursor Workspace\CCTV\Recordings",
        )

    def test_repair_plain_windows_path_unchanged(self):
        path = r"D:\NVR\Recordings"
        self.assertEqual(_repair_mixed_recordings_path(path), path)

    def test_normalize_windows_drive(self):
        with patch.object(os, "name", "nt"):
            resolved = normalize_recordings_path(r"C:\Temp\NVR\Recordings")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, Path(r"C:\Temp\NVR\Recordings").resolve())


if __name__ == "__main__":
    unittest.main()
