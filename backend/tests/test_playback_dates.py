import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_dates_endpoint, playback_client_config_endpoint
from app.services.app_timezone import clear_app_timezone_cache


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


class TestPlaybackClientConfig(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        clear_app_timezone_cache()

    async def test_requires_auth(self):
        request = make_mocked_request("GET", "/api/playback/client-config")
        response = await playback_client_config_endpoint(request)
        self.assertEqual(response.status, 401)

    async def test_returns_timezone(self):
        import os

        prev = os.environ.get("APP_TIMEZONE")
        os.environ["APP_TIMEZONE"] = "Asia/Kolkata"
        clear_app_timezone_cache()
        try:
            request = make_mocked_request("GET", "/api/playback/client-config")
            request["auth_user"] = {"_id": "u1", "role": "Admin", "name": "admin"}
            with patch(
                "app.routes.playback.get_effective_user",
                new_callable=AsyncMock,
                return_value=request["auth_user"],
            ):
                response = await playback_client_config_endpoint(request)
            self.assertEqual(response.status, 200)
            body = json.loads(response.text)
            self.assertEqual(body["timezone"], "Asia/Kolkata")
        finally:
            if prev is None:
                os.environ.pop("APP_TIMEZONE", None)
            else:
                os.environ["APP_TIMEZONE"] = prev
            clear_app_timezone_cache()


if __name__ == "__main__":
    unittest.main()
