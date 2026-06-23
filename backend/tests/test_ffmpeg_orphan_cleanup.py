import unittest
from unittest.mock import MagicMock, patch

from app.services.ffmpeg_orphan_cleanup import (
    NvrFfmpegProcess,
    _orphan_processes,
    cleanup_orphan_ffmpeg_on_startup,
    get_tracked_ffmpeg_pids,
    is_nvr_ffmpeg_cmdline,
)


class TestFfmpegOrphanHelpers(unittest.TestCase):
    def test_is_nvr_ffmpeg_cmdline_live(self):
        cmd = (
            "ffmpeg -i rtsp://x/102 -f hls "
            r"C:\Users\me\AppData\Local\Temp\nvr_live\cam1\live.m3u8"
        )
        self.assertTrue(is_nvr_ffmpeg_cmdline(cmd))

    def test_is_nvr_ffmpeg_cmdline_rejects_unrelated(self):
        self.assertFalse(is_nvr_ffmpeg_cmdline("ffmpeg -i input.mp4 out.mp4"))

    @patch("app.services.ffmpeg_orphan_cleanup.get_tracked_ffmpeg_pids")
    def test_orphan_when_parent_dead(self, mock_tracked):
        mock_tracked.return_value = {100}
        procs = [
            NvrFfmpegProcess(
                pid=100,
                parent_pid=1,
                parent_alive=True,
                cmdline="ffmpeg nvr_live/cam1/",
                stream_type="grid",
                status="tracked",
            ),
            NvrFfmpegProcess(
                pid=200,
                parent_pid=9999,
                parent_alive=False,
                cmdline="ffmpeg nvr_live/cam1/",
                stream_type="grid",
                status="orphan",
            ),
        ]
        orphans = _orphan_processes(procs)
        self.assertEqual([200], [p.pid for p in orphans])

    @patch("app.services.ffmpeg_orphan_cleanup._kill_pid_sync")
    @patch("app.services.ffmpeg_orphan_cleanup._orphan_processes")
    def test_startup_cleanup_kills_orphans(self, mock_orphans, mock_kill):
        mock_orphans.return_value = [
            NvrFfmpegProcess(
                pid=8324,
                parent_pid=956,
                parent_alive=False,
                cmdline="ffmpeg nvr_live/cam/",
                stream_type="grid",
                status="orphan",
            )
        ]
        mock_kill.return_value = True
        killed = cleanup_orphan_ffmpeg_on_startup()
        self.assertEqual(killed, [8324])
        mock_kill.assert_called_once_with(8324)


class TestTrackedPids(unittest.TestCase):
    @patch("app.services.ffmpeg_orphan_cleanup.REGISTRY")
    def test_tracked_from_registry(self, mock_registry):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 4242
        record = MagicMock()
        record.proc = proc
        mock_registry.all_records.return_value = [record]
        with patch.dict(
            "app.services.video_recording.ACTIVE_RECORDINGS",
            {},
            clear=True,
        ):
            pids = get_tracked_ffmpeg_pids()
        self.assertEqual(pids, {4242})


if __name__ == "__main__":
    unittest.main()
