import json
import unittest
from unittest.mock import AsyncMock, patch
from app.routes.cameras import get_camera_list
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

class TestCameraRoutes(unittest.IsolatedAsyncioTestCase):
    @patch('app.routes.cameras.get_camera_info')
    async def test_get_camera_list(self, mock_get_camera_info):
        # Mock the get_camera_info service
        mock_get_camera_info.return_value = [
            {
                "id": "cam1",
                "name": "Test Camera",
                "online": True,
                "ptz": False,
                "activity": False
            }
        ]

        # Create a mocked request
        request = make_mocked_request('GET', '/api/cameras')

        # Call the handler
        response = await get_camera_list(request)

        # Assert response is correct
        self.assertEqual(response.status, 200)
        response_data = json.loads(response.text)
        self.assertEqual(len(response_data), 1)
        self.assertEqual(response_data[0]['name'], 'Test Camera')

if __name__ == '__main__':
    unittest.main()
