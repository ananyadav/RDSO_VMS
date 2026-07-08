import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_media_endpoint
from app.services.recording_media import (
    RECORDING_FILE_NOT_FOUND,
    RecordingMediaError,
    resolve_recording_file,
    rewrite_playlist_urls,
    validate_filename,
)


class TestRecordingMediaValidation(unittest.TestCase):
    def test_validate_filename_allows_hls(self):
        validate_filename("index.m3u8")
        validate_filename("seg_00001.ts")

    def test_validate_filename_blocks_traversal(self):
        with self.assertRaises(RecordingMediaError):
            validate_filename("../secret.ts")
        with self.assertRaises(RecordingMediaError):
            validate_filename("..")

    def test_rewrite_playlist_urls(self):
        content = "#EXTM3U\nseg_00001.ts\nseg_00002.ts\n"
        out = rewrite_playlist_urls(content, "cam1", "sess1")
        self.assertIn("/api/playback/cam1/sess1/media/seg_00001.ts", out)
        self.assertIn("/api/playback/cam1/sess1/media/seg_00002.ts", out)


class TestRecordingMediaService(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_recording_file_not_found(self):
        with patch(
            "app.services.recording_media.validate_camera_media_ref",
            new_callable=AsyncMock,
        ), patch(
            "app.services.recording_media.resolve_session_dir",
            new_callable=AsyncMock,
        ) as mock_dir:
            tmp = Path(tempfile.mkdtemp())
            mock_dir.return_value = tmp
            with self.assertRaises(RecordingMediaError) as ctx:
                await resolve_recording_file("cam1", "sess1", "seg_00001.ts")
            self.assertEqual(ctx.exception.status, 404)
            self.assertEqual(ctx.exception.message, RECORDING_FILE_NOT_FOUND)


class TestPlaybackMediaRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.build_recording_media_response", new_callable=AsyncMock)
    async def test_playback_media_route(self, mock_build, _mock_pb, _mock_cam):
        from aiohttp import web

        mock_build.return_value = web.Response(text="ok", status=200)
        request = make_mocked_request(
            "GET",
            "/api/playback/cam1/sess1/media/index.m3u8",
            match_info={
                "cameraId": "cam1",
                "sessionId": "sess1",
                "filename": "index.m3u8",
            },
        )
        response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 200)
        mock_build.assert_awaited_once_with("cam1", "sess1", "index.m3u8", auth_query="")

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.build_recording_media_response", new_callable=AsyncMock)
    async def test_playback_media_not_found(self, mock_build, _mock_pb, _mock_cam):
        mock_build.side_effect = RecordingMediaError(RECORDING_FILE_NOT_FOUND, 404)
        request = make_mocked_request(
            "GET",
            "/api/playback/cam1/sess1/media/seg_00001.ts",
            match_info={
                "cameraId": "cam1",
                "sessionId": "sess1",
                "filename": "seg_00001.ts",
            },
        )
        response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
