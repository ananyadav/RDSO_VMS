"""Tests for multi-camera playback search and at-time resolve."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_multi_search_endpoint
from app.services.playback_search import (
    NO_FOOTAGE_AT_TIME,
    resolve_recording_at_time,
    search_recordings_multi,
)


REC_A = {
    "sessionId": "s1",
    "startTime": "2026-06-08T10:00:00+00:00",
    "endTime": "2026-06-08T11:00:00+00:00",
    "duration": 3600,
    "playlistUrl": "/api/playback/cam1/s1/media/index.m3u8",
    "playable": True,
    "error": None,
}

REC_B = {
    "sessionId": "s2",
    "startTime": "2026-06-08T12:00:00+00:00",
    "endTime": "2026-06-08T12:30:00+00:00",
    "duration": 1800,
    "playlistUrl": "/api/playback/cam2/s2/media/index.m3u8",
    "playable": True,
    "error": None,
}


class TestResolveRecordingAtTime(unittest.TestCase):
    def test_hit_inside_session(self):
        at = datetime(2026, 6, 8, 10, 15, tzinfo=timezone.utc)
        hit = resolve_recording_at_time([REC_A, REC_B], at)
        self.assertTrue(hit["ok"])
        self.assertEqual(hit["sessionId"], "s1")
        self.assertEqual(hit["offsetSeconds"], 900.0)

    def test_no_footage(self):
        at = datetime(2026, 6, 8, 11, 30, tzinfo=timezone.utc)
        miss = resolve_recording_at_time([REC_A], at)
        self.assertFalse(miss["ok"])
        self.assertEqual(miss["code"], "no_footage")
        self.assertEqual(miss["error"], NO_FOOTAGE_AT_TIME)


class TestSearchRecordingsMulti(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.playback_search.search_recordings_by_date", new_callable=AsyncMock)
    async def test_two_cameras_both_have_footage(self, mock_search):
        async def _side(ref, date):
            if ref == "cam1":
                return {
                    "cameraId": "cam1",
                    "cameraUid": "cam1",
                    "cameraName": "Cam 1",
                    "date": date,
                    "recordings": [REC_A],
                    "total": 1,
                }
            return {
                "cameraId": "cam2",
                "cameraUid": "cam2",
                "cameraName": "Cam 2",
                "date": date,
                "recordings": [REC_B],
                "total": 1,
            }

        mock_search.side_effect = _side
        at = datetime(2026, 6, 8, 10, 15, tzinfo=timezone.utc)
        result = await search_recordings_multi(["cam1", "cam2"], "2026-06-08", at=at)
        self.assertEqual(result["totalCameras"], 2)
        self.assertTrue(result["cameras"][0]["resolved"]["ok"])
        self.assertEqual(result["cameras"][0]["resolved"]["sessionId"], "s1")
        self.assertFalse(result["cameras"][1]["resolved"]["ok"])
        self.assertEqual(result["cameras"][1]["resolved"]["code"], "no_footage")

    @patch("app.services.playback_search.search_recordings_by_date", new_callable=AsyncMock)
    async def test_one_camera_no_sessions(self, mock_search):
        mock_search.side_effect = [
            {
                "cameraId": "cam1",
                "cameraUid": "cam1",
                "cameraName": "Cam 1",
                "date": "2026-06-08",
                "recordings": [REC_A],
                "total": 1,
            },
            {
                "cameraId": "cam2",
                "cameraUid": "cam2",
                "cameraName": "Cam 2",
                "date": "2026-06-08",
                "recordings": [],
                "total": 0,
            },
        ]
        at = datetime(2026, 6, 8, 10, 15, tzinfo=timezone.utc)
        result = await search_recordings_multi(["cam1", "cam2"], "2026-06-08", at=at)
        self.assertTrue(result["cameras"][0]["resolved"]["ok"])
        self.assertFalse(result["cameras"][1]["resolved"]["ok"])


class TestMultiSearchRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.search_recordings_multi", new_callable=AsyncMock)
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_multi_search_omits_unauthorized(self, _pb, mock_cam, mock_multi):
        async def _acl(_req, ref):
            if ref == "forbidden":
                from aiohttp import web

                return web.json_response({"error": "Camera access denied"}, status=403)
            return None

        mock_cam.side_effect = _acl
        mock_multi.return_value = {
            "date": "2026-06-08",
            "timezone": "Asia/Kolkata",
            "at": None,
            "cameras": [
                {
                    "cameraId": "cam1",
                    "cameraUid": "cam1",
                    "cameraName": "Cam 1",
                    "ok": True,
                    "recordings": [REC_A],
                    "total": 1,
                    "resolved": None,
                }
            ],
            "totalCameras": 1,
        }
        request = make_mocked_request(
            "GET",
            "/api/playback/multi-search?date=2026-06-08&cameraUid=cam1&cameraUid=forbidden",
        )
        response = await playback_multi_search_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["requestedCount"], 2)
        self.assertEqual(body["authorizedCount"], 1)
        mock_multi.assert_awaited_once()
        self.assertEqual(mock_multi.await_args.args[0], ["cam1"])
        # Unauthorized camera must not appear in payload
        uids = [c["cameraUid"] for c in body["cameras"]]
        self.assertNotIn("forbidden", uids)

    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_multi_search_requires_cameras(self, _pb):
        request = make_mocked_request("GET", "/api/playback/multi-search?date=2026-06-08")
        response = await playback_multi_search_endpoint(request)
        self.assertEqual(response.status, 400)

    @patch(
        "app.routes.playback.deny_unless_playback_permission",
        new_callable=AsyncMock,
    )
    async def test_multi_search_requires_auth(self, mock_pb):
        from aiohttp import web

        mock_pb.return_value = web.json_response({"error": "Authentication required"}, status=401)
        request = make_mocked_request(
            "GET",
            "/api/playback/multi-search?date=2026-06-08&cameraUid=cam1",
        )
        response = await playback_multi_search_endpoint(request)
        self.assertEqual(response.status, 401)


if __name__ == "__main__":
    unittest.main()
