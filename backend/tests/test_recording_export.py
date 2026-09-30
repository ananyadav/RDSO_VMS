"""Unit tests for interval recording export helpers and route ACL."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.playback import playback_export_endpoint
from app.services.recording_export import (
    _overlap_piece,
    compute_gaps,
    _local_dates_covering,
)


REC = {
    "sessionId": "s1",
    "startTime": "2026-06-08T10:00:00+00:00",
    "endTime": "2026-06-08T11:00:00+00:00",
    "duration": 3600,
    "playlistUrl": "/api/playback/cam1/s1/media/index.m3u8",
    "filePath": "cam1/sessions/s1",
    "playable": True,
}


class TestExportHelpers(unittest.TestCase):
    def test_overlap_trims_to_selected_interval(self):
        start = datetime(2026, 6, 8, 10, 15, tzinfo=timezone.utc)
        end = datetime(2026, 6, 8, 10, 45, tzinfo=timezone.utc)
        piece = _overlap_piece(REC, start, end)
        assert piece is not None
        self.assertEqual(piece["offsetSeconds"], 900.0)
        self.assertEqual(piece["durationSeconds"], 1800.0)

    def test_overlap_none_outside(self):
        start = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 8, 12, 30, tzinfo=timezone.utc)
        self.assertIsNone(_overlap_piece(REC, start, end))

    def test_gaps_between_pieces(self):
        range_start = datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
        range_end = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        pieces = [
            {
                "clipStart": "2026-06-08T10:00:00+00:00",
                "clipEnd": "2026-06-08T10:30:00+00:00",
            },
            {
                "clipStart": "2026-06-08T11:00:00+00:00",
                "clipEnd": "2026-06-08T12:00:00+00:00",
            },
        ]
        gaps = compute_gaps(range_start, range_end, pieces)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["reason"], "no_footage")

    def test_local_dates_covering(self):
        start = datetime(2026, 6, 8, 22, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc)
        dates = _local_dates_covering(start, end)
        self.assertGreaterEqual(len(dates), 1)


class TestExportRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.write_audit", new_callable=AsyncMock)
    @patch("app.routes.playback.build_export_archive", new_callable=AsyncMock)
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.get_effective_user", new_callable=AsyncMock, return_value={"role": "ADMIN"})
    async def test_export_success_zip(self, _user, _pb, _cam, mock_build, _audit):
        mock_build.return_value = (
            b"PK\x03\x04fakezip",
            {
                "successCount": 2,
                "requestedCount": 2,
                "cameras": [{"cameraUid": "cam1", "ok": True}, {"cameraUid": "cam2", "ok": True}],
                "start": "2026-06-08T10:00:00+00:00",
                "end": "2026-06-08T10:10:00+00:00",
            },
        )
        request = make_mocked_request("POST", "/api/playback/export")
        request._body = json.dumps(
            {
                "cameraUids": ["cam1", "cam2"],
                "start": "2026-06-08T10:00:00Z",
                "end": "2026-06-08T10:10:00Z",
            }
        ).encode("utf-8")

        # aiohttp mocked request may not support can_read_body/json — patch parse
        with patch(
            "app.routes.playback._parse_json_body",
            new_callable=AsyncMock,
            return_value={
                "cameraUids": ["cam1", "cam2"],
                "start": "2026-06-08T10:00:00Z",
                "end": "2026-06-08T10:10:00Z",
            },
        ):
            response = await playback_export_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        self.assertIn("playback-export-", response.headers.get("Content-Disposition", ""))
        mock_build.assert_awaited_once()

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_export_omits_unauthorized_cameras(self, _pb, mock_cam):
        async def _acl(_req, ref):
            if ref == "forbidden":
                from aiohttp import web

                return web.json_response({"error": "denied"}, status=403)
            return None

        mock_cam.side_effect = _acl
        with patch(
            "app.routes.playback._parse_json_body",
            new_callable=AsyncMock,
            return_value={
                "cameraUids": ["cam1", "forbidden"],
                "start": "2026-06-08T10:00:00Z",
                "end": "2026-06-08T10:10:00Z",
            },
        ), patch(
            "app.routes.playback.build_export_archive",
            new_callable=AsyncMock,
            return_value=(b"PK", {"successCount": 1, "requestedCount": 1, "cameras": [], "start": "", "end": ""}),
        ) as mock_build, patch(
            "app.routes.playback.write_audit", new_callable=AsyncMock
        ), patch(
            "app.routes.playback.get_effective_user",
            new_callable=AsyncMock,
            return_value={"role": "Operator"},
        ):
            request = make_mocked_request("POST", "/api/playback/export")
            response = await playback_export_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(mock_build.await_args.args[0], ["cam1"])

    @patch(
        "app.routes.playback.deny_unless_playback_permission",
        new_callable=AsyncMock,
    )
    async def test_export_requires_auth(self, mock_pb):
        from aiohttp import web

        mock_pb.return_value = web.json_response({"error": "Authentication required"}, status=401)
        with patch(
            "app.routes.playback._parse_json_body",
            new_callable=AsyncMock,
            return_value={
                "cameraUids": ["cam1"],
                "start": "2026-06-08T10:00:00Z",
                "end": "2026-06-08T10:10:00Z",
            },
        ):
            request = make_mocked_request("POST", "/api/playback/export")
            response = await playback_export_endpoint(request)
        self.assertEqual(response.status, 401)

    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_export_requires_interval(self, _pb):
        with patch(
            "app.routes.playback._parse_json_body",
            new_callable=AsyncMock,
            return_value={"cameraUids": ["cam1"]},
        ):
            request = make_mocked_request("POST", "/api/playback/export")
            response = await playback_export_endpoint(request)
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
