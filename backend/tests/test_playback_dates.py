import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_dates_endpoint


class TestPlaybackDatesRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.get_recording_dates_for_month", new_callable=AsyncMock)
    async def test_dates_success(self, mock_dates):
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

    async def test_dates_missing_params(self):
        request = make_mocked_request("GET", "/api/playback/dates?cameraId=cam1")
        response = await playback_dates_endpoint(request)
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
