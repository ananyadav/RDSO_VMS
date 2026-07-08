import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_dates_endpoint


class TestPlaybackDatesRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.get_recording_dates_for_month", new_callable=AsyncMock)
    async def test_dates_success(self, mock_dates, _mock_pb, _mock_cam):
        mock_dates.return_value = {
            "cameraId": "cam1",
            "year": 2026,
            "month": 6,
            "dates": ["2026-06-08", "2026-06-09"],
        }
        request = make_mocked_request(
            "GET", "/api/playback/dates?cameraId=cam1&year=2026&month=6"
        )
        response = await playback_dates_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(len(body["dates"]), 2)

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_dates_missing_params(self, _mock_pb, _mock_cam):
        request = make_mocked_request("GET", "/api/playback/dates?cameraId=cam1")
        response = await playback_dates_endpoint(request)
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
