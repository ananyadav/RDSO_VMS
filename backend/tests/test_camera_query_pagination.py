"""Tests for optional camera list pagination (RDSO 18.1.1)."""

import unittest
from unittest.mock import MagicMock

from app.services.camera_service import _parse_filters


class TestCameraQueryPagination(unittest.TestCase):
    def _request(self, query: str):
        req = MagicMock()
        req.rel_url.query = {}
        for part in query.split("&"):
            if not part:
                continue
            key, _, val = part.partition("=")
            req.rel_url.query[key] = val
        return req

    def test_parse_limit_and_offset(self):
        filters = _parse_filters(self._request("limit=100&offset=200"))
        self.assertEqual(filters["limit"], 100)
        self.assertEqual(filters["offset"], 200)

    def test_limit_capped_at_500(self):
        filters = _parse_filters(self._request("limit=1500"))
        self.assertEqual(filters["limit"], 500)

    def test_no_pagination_when_limit_absent(self):
        filters = _parse_filters(self._request("camera_group=floor_a"))
        self.assertNotIn("limit", filters)
        self.assertNotIn("offset", filters)


if __name__ == "__main__":
    unittest.main()
