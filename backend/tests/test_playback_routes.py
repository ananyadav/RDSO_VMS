import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_search_endpoint


class TestPlaybackRoutes(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.search_recordings_by_date", new_callable=AsyncMock)
    async def test_playback_search_success(self, mock_search, _mock_pb, _mock_cam):
        mock_search.return_value = {
            "cameraId": "cam1",
            "cameraName": "Cam10",
            "date": "2026-06-08",
            "recordings": [
                {
                    "sessionId": "sess1",
                    "startTime": "2026-06-08T12:00:00+00:00",
                    "endTime": "2026-06-08T12:30:00+00:00",
                    "duration": 1800,
                    "filePath": "cam1/sessions/sess1",
                    "playlistUrl": "/api/playback/cam1/sess1/media/index.m3u8",
                    "status": "stopped",
                    "segmentCount": 6,
                }
            ],
            "total": 1,
        }
        request = make_mocked_request(
            "GET",
            "/api/playback/search?cameraId=cam1&date=2026-06-08",
        )
        response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["cameraId"], "cam1")
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["recordings"]), 1)
        mock_search.assert_awaited_once_with("cam1", "2026-06-08")

    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_playback_search_missing_camera(self, _mock_pb):
        request = make_mocked_request("GET", "/api/playback/search?date=2026-06-08")
        response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 400)
        body = json.loads(response.text)
        self.assertIn("cameraId", body["error"])

    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_playback_search_missing_date(self, _mock_pb):
        request = make_mocked_request("GET", "/api/playback/search?cameraId=cam1")
        response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 400)
        body = json.loads(response.text)
        self.assertIn("date", body["error"])

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_playback_search_invalid_date(self, _mock_pb, _mock_cam):
        request = make_mocked_request(
            "GET", "/api/playback/search?cameraId=cam1&date=08-06-2026"
        )
        response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 400)

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.search_recordings_by_date", new_callable=AsyncMock)
    async def test_playback_search_camera_not_found(self, mock_search, _mock_pb, _mock_cam):
        mock_search.return_value = {"error": "Camera not found", "status": 404}
        request = make_mocked_request(
            "GET", "/api/playback/search?cameraId=bad&date=2026-06-08"
        )
        response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
