import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.playback_search import (
    RECORDING_FILE_NOT_FOUND,
    _build_recording_entry,
    _has_playable_media,
    _interval_overlaps_day,
    _parse_date,
    search_recordings_by_date,
)


class TestPlaybackSearchHelpers(unittest.TestCase):
    def test_parse_date(self):
        start, end = _parse_date("2026-06-08")
        self.assertEqual(start, datetime(2026, 6, 8, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 9, tzinfo=timezone.utc))

    def test_interval_overlaps_day(self):
        day_start, day_end = _parse_date("2026-06-08")
        inside = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
        outside = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(
            _interval_overlaps_day(inside, inside, day_start, day_end)
        )
        self.assertFalse(
            _interval_overlaps_day(outside, outside, day_start, day_end)
        )

    def test_has_playable_media_requires_segments(self):
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            self.assertFalse(_has_playable_media(session_dir))

            (session_dir / "seg_00000.ts").write_bytes(b"1234")
            self.assertTrue(_has_playable_media(session_dir))


class TestBuildRecordingEntry(unittest.TestCase):
    @patch("app.services.playback_search._session_status", return_value="stopped")
    @patch("app.services.playback_search._resolve_playback_session_dir")
    def test_mongodb_session_missing_files_excluded_from_search(
        self, mock_resolve_dir, _mock_status
    ):
        mock_resolve_dir.return_value = None
        day_start, day_end = _parse_date("2026-06-08")
        doc = {
            "started_at": "2026-06-08T10:00:00+00:00",
            "stopped_at": "2026-06-08T11:00:00+00:00",
            "storage_path": "cam1/sessions/sess1",
            "status": "stopped",
        }
        entry = _build_recording_entry(
            "cam1",
            "sess1",
            doc=doc,
            day_start=day_start,
            day_end=day_end,
        )
        self.assertIsNone(entry)

    @patch("app.services.playback_search._session_status", return_value="stopped")
    @patch("app.services.playback_search._segment_bounds")
    @patch("app.services.playback_search._resolve_playback_session_dir")
    def test_filesystem_fallback_without_mongodb(
        self, mock_resolve_dir, mock_bounds, _mock_status
    ):
        from datetime import datetime, timezone
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            (session_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (session_dir / "seg_00000.ts").write_bytes(b"1234")
            mock_resolve_dir.return_value = session_dir
            first = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
            last = datetime(2026, 6, 8, 11, 0, tzinfo=timezone.utc)
            mock_bounds.return_value = (first, last, 1)
            day_start, day_end = _parse_date("2026-06-08")
            entry = _build_recording_entry(
                "cam1",
                "sess1",
                doc=None,
                day_start=day_start,
                day_end=day_end,
            )
            self.assertIsNotNone(entry)
            self.assertTrue(entry["playable"])
            self.assertEqual(entry["metadataSource"], "filesystem")
            self.assertIsNone(entry["error"])


class TestPlaybackSearchService(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.playback_search._resolve_camera_name", new_callable=AsyncMock)
    @patch("app.services.playback_search.recording_sessions_collection")
    @patch("app.services.playback_search.get_effective_recordings_dir")
    async def test_search_filters_by_date(
        self, mock_recordings_dir, mock_collection, mock_camera_name
    ):
        mock_camera_name.return_value = "Cam10"
        mock_recordings_dir.return_value.__truediv__ = MagicMock(
            return_value=MagicMock(is_dir=MagicMock(return_value=False))
        )

        doc = {
            "_id": "sess1",
            "camera_id": "cam1",
            "status": "stopped",
            "started_at": "2026-06-08T10:00:00+00:00",
            "stopped_at": "2026-06-08T11:00:00+00:00",
            "storage_path": "cam1/sessions/sess1",
        }

        async def _aiter():
            yield doc

        mock_collection.find.return_value.sort.return_value = _aiter()

        with patch(
            "app.services.playback_search.session_storage_dir",
            return_value=MagicMock(is_dir=MagicMock(return_value=False)),
        ), patch(
            "app.services.playback_search._build_recording_entry",
            return_value={
                "sessionId": "sess1",
                "startTime": "2026-06-08T10:00:00+00:00",
                "endTime": "2026-06-08T11:00:00+00:00",
                "duration": 3600,
                "filePath": "cam1/sessions/sess1",
                "playlistUrl": "/api/playback/cam1/sess1/media/index.m3u8",
                "status": "stopped",
                "segmentCount": 2,
            },
        ):
            result = await search_recordings_by_date("cam1", "2026-06-08")

        self.assertEqual(result["cameraId"], "cam1")
        self.assertEqual(result["cameraName"], "Cam10")
        self.assertEqual(result["date"], "2026-06-08")
        self.assertEqual(result["total"], 1)


if __name__ == "__main__":
    unittest.main()
