import unittest
from unittest.mock import AsyncMock, patch

from app.services.camera_form import find_duplicate_camera


class TestCameraDuplicate(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.camera_form.find_existing_by_ip", new_callable=AsyncMock)
    async def test_same_name_different_ip_allowed(self, mock_by_ip):
        mock_by_ip.return_value = None
        dup = await find_duplicate_camera(
            {
                "name": "Entry Gate",
                "ip_address": "192.168.1.50",
            }
        )
        self.assertIsNone(dup)

    @patch("app.services.camera_form.find_existing_by_ip", new_callable=AsyncMock)
    async def test_duplicate_ip_blocked(self, mock_by_ip):
        mock_by_ip.return_value = {"_id": "abc", "name": "Other", "ip_address": "192.168.1.50"}
        dup = await find_duplicate_camera({"name": "Entry Gate", "ip_address": "192.168.1.50"})
        self.assertIsNotNone(dup)
        self.assertEqual(dup[1], "ip_address")


if __name__ == "__main__":
    unittest.main()
