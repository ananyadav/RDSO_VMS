import os
import sys
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
        if sys.platform == "win32":
            self.assertEqual(_repair_mixed_recordings_path(path), path)
        else:
            # On Linux, pure Windows paths are rejected (caller falls back).
            self.assertEqual(_repair_mixed_recordings_path(path), "")

    @unittest.skipUnless(sys.platform == "win32", "WindowsPath only available on Windows")
    def test_normalize_windows_drive(self):
        # Do not patch os.name: pathlib.Path checks os.name at construction time and
        # raises NotImplementedError for WindowsPath on Linux CI runners.
        resolved = normalize_recordings_path(r"C:\Temp\NVR\Recordings")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, Path(r"C:\Temp\NVR\Recordings").resolve())

    @unittest.skipUnless(sys.platform != "win32", "POSIX path normalization on Linux/macOS")
    def test_normalize_posix_path(self):
        resolved = normalize_recordings_path("/tmp/nvr-recordings-ci")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, Path("/tmp/nvr-recordings-ci").resolve())

    @unittest.skipIf(sys.platform == "win32", "Linux-only: Windows path must fall back")
    def test_windows_path_on_linux_falls_back_to_default(self):
        win = r"C:\Users\Ananya Yadav\Cursor Workspace\CCTV\Recordings"
        self.assertEqual(_repair_mixed_recordings_path(win), "")
        resolved = normalize_recordings_path(win)
        self.assertTrue(resolved.is_absolute())
        # Should not raise and should not keep the Windows drive letter.
        self.assertFalse(str(resolved).startswith("C:"))
        self.assertIn("Recordings", str(resolved).replace("\\", "/"))

    @unittest.skipUnless(sys.platform == "win32", "Windows-only: Linux path must fall back")
    def test_linux_path_on_windows_falls_back_to_default(self):
        linux = "/home/vms/cctv_ananya/CCTV/Recordings"
        self.assertEqual(_repair_mixed_recordings_path(linux), "")
        resolved = normalize_recordings_path(linux)
        self.assertTrue(resolved.is_absolute())
        self.assertIn("Recordings", str(resolved))


if __name__ == "__main__":
    unittest.main()
