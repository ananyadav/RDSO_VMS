"""RDSO 18.1.12 — configurable recording storage / volume health."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.storage_settings_store import (
    apply_recordings_dir,
    get_effective_recordings_dir,
)
from app.services.storage_volume import (
    STATUS_CRITICAL,
    STATUS_LOW_SPACE,
    STATUS_ONLINE,
    STATUS_READ_ONLY,
    STATUS_UNAVAILABLE,
    StorageInsufficientSpaceError,
    StorageReadOnlyError,
    StorageUnavailableError,
    assert_storage_ready_for_recording,
    disk_payload_from_probe,
    probe_storage_path,
)


class ProbeWritableTests(unittest.TestCase):
    def test_writable_temp_dir_is_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(500 * 1024**3, 100 * 1024**3, 400 * 1024**3, None),
            ), patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "1",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertTrue(probe["exists"])
            self.assertTrue(probe["writable"])
            self.assertEqual(probe["status"], STATUS_ONLINE)
            self.assertTrue(probe["allow_recording"])
            self.assertEqual(probe["storage_model"], "os_filesystem")
            disk = disk_payload_from_probe(probe)
            self.assertEqual(disk["status_label"], "Online")
            self.assertGreater(disk["disk_total_gb"], 0)

    def test_missing_path_unavailable_no_fallback(self):
        missing = Path(tempfile.gettempdir()) / f"vms_missing_mount_{os.getpid()}_nope"
        if missing.exists():
            missing.rmdir()
        probe = probe_storage_path(missing, create_if_missing=False)
        self.assertEqual(probe["status"], STATUS_UNAVAILABLE)
        self.assertFalse(probe["allow_recording"])
        self.assertIsNone(probe["total_bytes"])  # must not invent another volume

    def test_disk_usage_failure_does_not_substitute_other_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(None, None, None, "boom"),
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_UNAVAILABLE)
            self.assertIn("no fallback", (probe.get("error") or "").lower())
            self.assertEqual(probe["disk_total_gb"], 0.0)

    @unittest.skipIf(sys.platform == "win32", "POSIX chmod read-only probe")
    def test_read_only_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            path.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                probe = probe_storage_path(path)
                self.assertEqual(probe["status"], STATUS_READ_ONLY)
                self.assertFalse(probe["writable"])
                self.assertFalse(probe["allow_recording"])
            finally:
                path.chmod(stat.S_IRWXU)

    def test_low_space_insufficient_blocks_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "100000",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_CRITICAL)
            self.assertEqual(probe["status_label"], "Critical")
            self.assertFalse(probe["allow_recording"])
            disk = disk_payload_from_probe(probe)
            self.assertEqual(disk["status"], STATUS_CRITICAL)
            self.assertIn("percent_free", disk)
            self.assertIn("percent_used", disk)

    def test_low_space_warning_still_allows_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Force free% into warning band without hitting hard min free.
            with patch(
                "app.services.storage_volume._disk_usage_bytes",
                return_value=(1000 * 1024**3, 880 * 1024**3, 120 * 1024**3, None),
            ), patch.dict(
                os.environ,
                {
                    "RECORDING_MIN_FREE_GB": "1",
                    "RECORDING_LOW_SPACE_PERCENT": "20",
                    "RECORDING_CRITICAL_FREE_PERCENT": "5",
                },
            ):
                probe = probe_storage_path(tmp)
            self.assertEqual(probe["status"], STATUS_LOW_SPACE)
            self.assertTrue(probe["allow_recording"])
            self.assertEqual(probe["status_level"], "yellow")


class ConfigurableRootTests(unittest.TestCase):
    def tearDown(self):
        # Restore default project recordings dir for other tests in-process.
        default = Path(__file__).resolve().parents[2].parent / "Recordings"
        try:
            apply_recordings_dir(default, validate=False)
        except Exception:
            pass

    def test_apply_alternate_storage_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = apply_recordings_dir(tmp, validate=True)
            self.assertEqual(resolved, Path(tmp).resolve())
            self.assertEqual(get_effective_recordings_dir(), resolved)
            probe = probe_storage_path(resolved)
            self.assertTrue(probe["writable"])
            self.assertIn(probe["status"], (STATUS_ONLINE, STATUS_LOW_SPACE, STATUS_CRITICAL))
            # Even on a low host disk, writable configured root must not become Unavailable
            self.assertNotEqual(probe["status"], STATUS_UNAVAILABLE)

    def test_apply_rejects_unwritable(self):
        missing = Path(tempfile.gettempdir()) / f"vms_ro_reject_{os.getpid()}"
        with patch(
            "app.services.storage_volume.probe_storage_path",
            return_value={
                "path": str(missing),
                "writable": False,
                "error": "not writable",
                "status": STATUS_READ_ONLY,
            },
        ):
            with self.assertRaises(ValueError):
                apply_recordings_dir(missing, validate=True)


class AssertReadyTests(unittest.TestCase):
    def test_assert_raises_unavailable(self):
        with patch(
            "app.services.storage_volume.probe_recordings_storage",
            return_value={
                "allow_recording": False,
                "status": STATUS_UNAVAILABLE,
                "error": "gone",
            },
        ):
            with self.assertRaises(StorageUnavailableError):
                assert_storage_ready_for_recording()

    def test_assert_raises_readonly(self):
        with patch(
            "app.services.storage_volume.probe_recordings_storage",
            return_value={
                "allow_recording": False,
                "status": STATUS_READ_ONLY,
                "error": "ro",
            },
        ):
            with self.assertRaises(StorageReadOnlyError):
                assert_storage_ready_for_recording()

    def test_assert_raises_insufficient(self):
        with patch(
            "app.services.storage_volume.probe_recordings_storage",
            return_value={
                "allow_recording": False,
                "status": STATUS_LOW_SPACE,
                "error": "full",
            },
        ):
            with self.assertRaises(StorageInsufficientSpaceError):
                assert_storage_ready_for_recording()


class StartRecordingStorageGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        from app.services.video_recording import ACTIVE_RECORDINGS

        ACTIVE_RECORDINGS.clear()

    async def test_start_blocked_before_session_when_storage_bad(self):
        from app.services.video_recording import start_camera_recording

        with patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.video_recording.get_active_recording_session",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.video_recording.camera_collection"
        ) as cams, patch(
            "app.services.video_recording.create_recording_session",
            new_callable=AsyncMock,
        ) as create_sess, patch(
            "app.services.video_recording.ObjectId",
            side_effect=lambda x: x,
        ), patch(
            "app.services.storage_volume.assert_storage_ready_for_recording",
            side_effect=StorageUnavailableError("gone", probe={"status": STATUS_UNAVAILABLE}),
        ):
            cams.find_one = AsyncMock(
                return_value={
                    "_id": "507f1f77bcf86cd799439011",
                    "name": "t",
                    "ip_address": "10.0.0.1",
                    "camera_uid": "ip_10_0_0_1",
                }
            )
            with self.assertRaises(StorageUnavailableError):
                await start_camera_recording("507f1f77bcf86cd799439011")
            create_sess.assert_not_called()

    async def test_session_paths_use_configured_root(self):
        from app.services.video_recording import ACTIVE_RECORDINGS, start_camera_recording

        with tempfile.TemporaryDirectory() as tmp:
            root = apply_recordings_dir(tmp, validate=True)

            async def _create(*_a, **kw):
                return {
                    "id": "sess1",
                    "started_at": "t",
                    "storage_path": kw.get("storage_path"),
                }

            updates: list = []

            async def _update(_sid, patch_doc):
                updates.append(patch_doc)

            class FakeRecorder:
                def __init__(self, *a, **k):
                    self.storage_folder = k.get("storage_folder")
                    self.is_recording = True

                async def start_recording(self):
                    d = root / self.storage_folder / "sessions" / "sess1"
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

            with patch(
                "app.services.recording_config.is_recording_engine_enabled",
                return_value=True,
            ), patch(
                "app.services.video_recording.get_active_recording_session",
                new_callable=AsyncMock,
                return_value=None,
            ), patch(
                "app.services.video_recording.camera_collection"
            ) as cams, patch(
                "app.services.video_recording.create_recording_session",
                side_effect=_create,
            ), patch(
                "app.services.video_recording.update_recording_session",
                side_effect=_update,
            ), patch(
                "app.services.video_recording.build_camera_rtsp_urls",
                return_value={"main": "rtsp://x"},
            ), patch(
                "app.services.video_recording.resolve_recording_rtsp_url",
                return_value=("rtsp://x", "main"),
            ), patch(
                "app.services.video_recording.ObjectId",
                side_effect=lambda x: x,
            ), patch(
                "app.services.video_recording.VideoRecorder",
                FakeRecorder,
            ):
                cams.find_one = AsyncMock(
                    return_value={
                        "_id": "507f1f77bcf86cd799439011",
                        "name": "t",
                        "ip_address": "192.168.41.31",
                        "camera_uid": "ip_192_168_41_31",
                    }
                )
                session = await start_camera_recording("507f1f77bcf86cd799439011")

            self.assertEqual(session["storage_path"], "ip_192_168_41_31/sessions/sess1")
            self.assertEqual(session["recordings_root"], str(root))
            self.assertTrue(str(session["storage_absolute_path"]).startswith(str(root)))
            self.assertTrue(
                (root / "ip_192_168_41_31" / "sessions" / "sess1" / "index.m3u8").is_file()
            )
            from app.services.video_recording import session_storage_dir

            with patch("app.services.video_recording.RECORDINGS_DIR", root):
                resolved = session_storage_dir("ip_192_168_41_31", "sess1")
            self.assertEqual(resolved, root / "ip_192_168_41_31" / "sessions" / "sess1")
            ACTIVE_RECORDINGS.clear()


class DashboardNoFallbackTests(unittest.TestCase):
    def test_dashboard_disk_uses_probe_without_c_drive_fallback(self):
        from app.services.storage_dashboard import _disk_usage_for_recordings

        fake = {
            "path": r"Z:\missing-nas",
            "status": STATUS_UNAVAILABLE,
            "status_label": "Unavailable",
            "status_level": "red",
            "disk_total_gb": 0.0,
            "disk_used_gb": 0.0,
            "disk_free_gb": 0.0,
            "disk_free_percent": 0.0,
            "disk_percent": 0.0,
            "writable": False,
            "allow_recording": False,
            "error": "no fallback",
            "storage_model": "os_filesystem",
        }
        with patch(
            "app.services.storage_volume.probe_recordings_storage",
            return_value=fake,
        ):
            disk = _disk_usage_for_recordings()
        self.assertEqual(disk["status"], STATUS_UNAVAILABLE)
        self.assertEqual(disk["disk_total_gb"], 0.0)
        self.assertNotEqual(disk["disk_path"].lower()[:3], "c:\\")


if __name__ == "__main__":
    unittest.main()
