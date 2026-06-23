import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.recording import (
    start_recording_endpoint,
    stop_recording_endpoint,
    list_camera_sessions_endpoint,
)


class TestRecordingRoutes(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.recording.start_camera_recording", new_callable=AsyncMock)
    async def test_start_recording(self, mock_start):
        mock_start.return_value = {
            "id": "sess1",
            "camera_id": "cam1",
            "status": "recording",
        }
        request = make_mocked_request(
            "POST", "/api/recordings/cam1/start", match_info={"cameraId": "cam1"}
        )
        response = await start_recording_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["status"], "recording")
        mock_start.assert_awaited_once_with("cam1")

    @patch("app.routes.recording.stop_camera_recording", new_callable=AsyncMock)
    async def test_stop_recording(self, mock_stop):
        mock_stop.return_value = {"id": "sess1", "status": "stopped"}
        request = make_mocked_request(
            "POST", "/api/recordings/cam1/stop", match_info={"cameraId": "cam1"}
        )
        response = await stop_recording_endpoint(request)
        self.assertEqual(response.status, 200)
        mock_stop.assert_awaited_once_with("cam1")

    @patch("app.routes.recording.list_recording_sessions", new_callable=AsyncMock)
    async def test_list_sessions(self, mock_list):
        mock_list.return_value = [{"id": "sess1"}]
        request = make_mocked_request(
            "GET", "/api/recordings/cam1/sessions", match_info={"cameraId": "cam1"}
        )
        response = await list_camera_sessions_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(len(body["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
