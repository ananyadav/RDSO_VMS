import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.live_stream_registry import (
    LiveStreamRegistry,
    StreamRecord,
    StreamStatus,
)


class TestLiveStreamRegistry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = LiveStreamRegistry()
        self.playlist = Path("/tmp/nvr_live/cam1/live.m3u8")

    def _mock_proc(self, pid: int = 1234):
        proc = MagicMock()
        proc.pid = pid
        proc.returncode = None
        return proc

    def test_mark_reused_increments_ref_count(self):
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=self.playlist,
            proc=self._mock_proc(),
            status=StreamStatus.RUNNING,
            ref_count=1,
        )
        self.registry.mark_reused(record)
        self.assertEqual(record.ref_count, 2)

    def test_mark_reused_cancels_warming(self):
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=self.playlist,
            proc=self._mock_proc(),
            status=StreamStatus.WARMING,
            ref_count=0,
        )
        warm_task = MagicMock()
        warm_task.done.return_value = False
        record.warm_stop_task = warm_task
        self.registry.mark_reused(record)
        self.assertEqual(record.status, StreamStatus.RUNNING)
        self.assertEqual(record.ref_count, 1)
        warm_task.cancel.assert_called_once()

    def test_release_ref_schedules_warm_only_when_zero(self):
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=self.playlist,
            proc=self._mock_proc(),
            status=StreamStatus.RUNNING,
            ref_count=2,
        )
        should_warm = self.registry.release_ref(record)
        self.assertFalse(should_warm)
        self.assertEqual(record.ref_count, 1)

        should_warm = self.registry.release_ref(record)
        self.assertTrue(should_warm)
        self.assertEqual(record.ref_count, 0)

    @patch("app.services.live_stream_registry.WARM_SECONDS", 0.05)
    async def test_warm_stop_kills_after_timeout(self):
        stop_callback = AsyncMock()
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=self.playlist,
            proc=self._mock_proc(),
            status=StreamStatus.RUNNING,
            ref_count=1,
        )
        self.registry._streams["cam1"] = record
        self.registry.release_ref(record)
        self.registry.schedule_warm_stop(record, stop_callback)
        await asyncio.sleep(0.12)
        stop_callback.assert_awaited_once_with("cam1")

    @patch("app.services.live_stream_registry.WARM_SECONDS", 0.05)
    async def test_resubscribe_during_warm_cancels_stop(self):
        stop_callback = AsyncMock()
        record = StreamRecord(
            stream_id="cam1",
            playlist_path=self.playlist,
            proc=self._mock_proc(),
            status=StreamStatus.RUNNING,
            ref_count=1,
        )
        self.registry._streams["cam1"] = record
        self.registry.release_ref(record)
        self.registry.schedule_warm_stop(record, stop_callback)
        self.registry.mark_reused(record)
        await asyncio.sleep(0.12)
        stop_callback.assert_not_awaited()

    async def test_per_stream_lock_prevents_duplicate_starts(self):
        lock = self.registry.lock("cam1")
        lock2 = self.registry.lock("cam1")
        self.assertIs(lock, lock2)

        other = self.registry.lock("cam2")
        self.assertIsNot(lock, other)


if __name__ == "__main__":
    unittest.main()
