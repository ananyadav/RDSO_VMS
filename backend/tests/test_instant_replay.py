"""Instant Replay resolver + route security tests."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import make_mocked_request

from app.services.instant_replay_resolve import (
    resolve_instant_replay_from_recordings,
    resolve_instant_replay,
)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestInstantReplayResolve(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_30_sec_ago_inside_recording(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "sess_active",
                "folderId": "ip_10_0_0_1",
                "started": now - timedelta(minutes=10),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1", "name": "Cam1", "ip_address": "10.0.0.1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_10_0_0_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam1",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
        ):
            result = await resolve_instant_replay_from_recordings("cam1", seconds_ago=30)

        self.assertTrue(result["ok"])
        self.assertEqual(result["sessionId"], "sess_active")
        self.assertEqual(result["sourceType"], "recording")
        self.assertAlmostEqual(result["offsetSeconds"], 570.0, places=1)
        self.assertIn("/api/playback/ip_10_0_0_1/sess_active/media/index.m3u8", result["playlistUrl"])

    async def test_resolve_absolute_timestamp(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        target = now - timedelta(seconds=45)
        sessions = [
            {
                "sessionId": "sess1",
                "folderId": "folderA",
                "started": now - timedelta(minutes=5),
                "stopped": now,
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
        ):
            result = await resolve_instant_replay_from_recordings(
                "cam1", at_iso=target.isoformat()
            )
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["offsetSeconds"], 255.0, places=1)

    async def test_active_and_completed_sessions(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "active",
                "folderId": "f",
                "started": now - timedelta(minutes=2),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
            {
                "sessionId": "old",
                "folderId": "f",
                "started": now - timedelta(minutes=20),
                "stopped": now - timedelta(minutes=10),
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
        ):
            active = await resolve_instant_replay_from_recordings("cam1", seconds_ago=30)
            completed = await resolve_instant_replay_from_recordings(
                "cam1", at_iso=(now - timedelta(minutes=15)).isoformat()
            )
        self.assertEqual(active["sessionId"], "active")
        self.assertEqual(completed["sessionId"], "old")

    async def test_multiple_sessions_pick_covering(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "s2",
                "folderId": "f",
                "started": now - timedelta(minutes=5),
                "stopped": now,
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
            {
                "sessionId": "s1",
                "folderId": "f",
                "started": now - timedelta(minutes=30),
                "stopped": now - timedelta(minutes=20),
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
        ):
            r = await resolve_instant_replay_from_recordings(
                "cam1", at_iso=(now - timedelta(minutes=25)).isoformat()
            )
        self.assertEqual(r["sessionId"], "s1")

    async def test_gap_returns_no_footage(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "early",
                "folderId": "f",
                "started": now - timedelta(hours=2),
                "stopped": now - timedelta(hours=1),
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
            {
                "sessionId": "late",
                "folderId": "f",
                "started": now - timedelta(minutes=5),
                "stopped": now,
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            },
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
            patch(
                "app.services.instant_replay_buffer.resolve_buffer_offset",
                return_value=None,
            ),
            patch(
                "app.services.instant_replay_buffer.get_buffer_window",
                return_value={"active": False, "oldestAvailableAt": None, "newestAvailableAt": None, "segmentCount": 0},
            ),
        ):
            r = await resolve_instant_replay(
                "cam1", at_iso=(now - timedelta(minutes=30)).isoformat()
            )
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_footage")

    async def test_before_oldest_rejected(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "s",
                "folderId": "f",
                "started": now - timedelta(minutes=5),
                "stopped": now,
                "status": "stopped",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
        ):
            r = await resolve_instant_replay_from_recordings(
                "cam1", at_iso=(now - timedelta(hours=1)).isoformat()
            )
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_footage")

    async def test_buffer_fallback_when_no_recording(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        oldest = now - timedelta(seconds=120)
        newest = now
        with (
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1"},
            ),
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            ),
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            ),
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            ),
            patch(
                "app.services.instant_replay_buffer.resolve_buffer_offset",
                return_value={
                    "ok": True,
                    "sourceType": "instant_replay_buffer",
                    "sessionId": None,
                    "playlistUrl": "/api/playback/instant-replay-buffer/ip_1/media/index.m3u8",
                    "offsetSeconds": 90.0,
                    "oldestAvailableAt": oldest.isoformat(),
                    "newestAvailableAt": newest.isoformat(),
                    "status": "buffering",
                },
            ),
            patch(
                "app.services.instant_replay_buffer.get_buffer_window",
                return_value={
                    "active": True,
                    "oldestAvailableAt": oldest.isoformat(),
                    "newestAvailableAt": newest.isoformat(),
                    "segmentCount": 60,
                },
            ),
        ):
            r = await resolve_instant_replay("cam1", seconds_ago=30)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sourceType"], "instant_replay_buffer")
        self.assertIn("instant-replay-buffer", r["playlistUrl"])


class TestInstantReplayRoutes(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.resolve_instant_replay", new_callable=AsyncMock)
    async def test_resolve_route_ok(self, mock_resolve, _pb, _cam):
        from app.routes.playback import instant_replay_resolve_endpoint

        mock_resolve.return_value = {
            "ok": True,
            "sessionId": "s1",
            "playlistUrl": "/api/playback/f/s1/media/index.m3u8",
            "offsetSeconds": 10,
        }
        request = make_mocked_request(
            "GET", "/api/playback/instant-replay/resolve?cameraId=cam1&secondsAgo=30"
        )
        response = await instant_replay_resolve_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertTrue(body["ok"])

    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock)
    async def test_recording_view_enforced(self, mock_pb):
        from aiohttp import web
        from app.routes.playback import instant_replay_resolve_endpoint

        mock_pb.return_value = web.json_response({"error": "Forbidden"}, status=403)
        request = make_mocked_request(
            "GET", "/api/playback/instant-replay/resolve?cameraId=cam1&secondsAgo=30"
        )
        response = await instant_replay_resolve_endpoint(request)
        self.assertEqual(response.status, 403)

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_camera_acl_enforced(self, _pb, mock_cam):
        from aiohttp import web
        from app.routes.playback import instant_replay_resolve_endpoint

        mock_cam.return_value = web.json_response({"error": "Forbidden"}, status=403)
        request = make_mocked_request(
            "GET", "/api/playback/instant-replay/resolve?cameraId=cam1&secondsAgo=30"
        )
        response = await instant_replay_resolve_endpoint(request)
        self.assertEqual(response.status, 403)

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    async def test_buffer_media_requires_auth_path(self, _pb, _cam):
        from app.routes.playback import instant_replay_buffer_media_endpoint
        from app.services.recording_media import RecordingMediaError

        with (
            patch(
                "app.routes.playback.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_10_0_0_1",
            ),
            patch(
                "app.routes.playback.build_instant_replay_buffer_media_response",
                new_callable=AsyncMock,
                side_effect=RecordingMediaError("Recording file not found", 404),
            ),
        ):
            request = make_mocked_request(
                "GET",
                "/api/playback/instant-replay-buffer/ip_10_0_0_1/media/index.m3u8",
            )
            # match_info for mocked request
            request = make_mocked_request(
                "GET",
                "/api/playback/instant-replay-buffer/ip_10_0_0_1/media/index.m3u8",
                match_info={"cameraUid": "ip_10_0_0_1", "filename": "index.m3u8"},
            )
            response = await instant_replay_buffer_media_endpoint(request)
        self.assertEqual(response.status, 404)


class TestInstantReplayBufferLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.services import instant_replay_buffer as buf

        await buf.cleanup_all_instant_replay_buffers(reason="test_setup")

    async def asyncTearDown(self):
        from app.services import instant_replay_buffer as buf

        await buf.cleanup_all_instant_replay_buffers(reason="test_teardown")

    async def test_one_buffer_shared_by_leases(self):
        from app.services import instant_replay_buffer as buf

        proc = MagicMock()
        proc.returncode = None
        proc.pid = 4242
        proc.stderr = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch(
            "app.services.instant_replay_buffer._start_ffmpeg",
            new_callable=AsyncMock,
        ) as start:
            start.return_value = {
                "camera_uid": "ip_1",
                "process": proc,
                "dir": MagicMock(),
                "started_at": datetime.now(timezone.utc),
                "leases": {},
                "idle_since": None,
                "stderr_task": None,
            }
            a = await buf.acquire_buffer_lease("ip_1", {"camera_uid": "ip_1"}, "lease-a")
            b = await buf.acquire_buffer_lease("ip_1", {"camera_uid": "ip_1"}, "lease-b")
        self.assertTrue(a["ok"])
        self.assertTrue(b["ok"])
        self.assertEqual(start.await_count, 1)
        self.assertEqual(buf.buffer_process_count_for_tests(), 1)
        self.assertEqual(buf.lease_count_for_camera("ip_1"), 2)

        await buf.release_buffer_lease("ip_1", "lease-a")
        self.assertEqual(buf.lease_count_for_camera("ip_1"), 1)

    async def test_resolve_buffer_offset_and_bounds(self):
        from app.services import instant_replay_buffer as buf
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # two segments with mtimes
            s1 = d / "seg_00001.ts"
            s2 = d / "seg_00002.ts"
            s1.write_bytes(b"x" * 10)
            s2.write_bytes(b"y" * 10)
            now = datetime.now(timezone.utc)
            import os

            os.utime(s1, (now.timestamp() - 60, now.timestamp() - 60))
            os.utime(s2, (now.timestamp() - 10, now.timestamp() - 10))

            buf._BUFFERS["ip_test"] = {
                "camera_uid": "ip_test",
                "process": MagicMock(returncode=None),
                "dir": d,
                "leases": {"l1": 0},
                "idle_since": None,
                "stderr_task": None,
            }
            oldest, newest, count = buf._segment_bounds(d)
            self.assertEqual(count, 2)
            self.assertIsNotNone(oldest)
            hit = buf.resolve_buffer_offset("ip_test", oldest + timedelta(seconds=30))
            self.assertIsNotNone(hit)
            self.assertTrue(hit["ok"])
            miss = buf.resolve_buffer_offset(
                "ip_test", oldest - timedelta(seconds=120)
            )
            self.assertIsNone(miss)
            buf._BUFFERS.pop("ip_test", None)


class TestInstantReplaySourceSelection(unittest.IsolatedAsyncioTestCase):
    """Permanent 300s HLS must not suppress short IR buffer for recent lookbacks."""

    def _common_patches(self, now, sessions, buf_offset, buf_window):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.services.instant_replay_resolve.get_camera_by_ref",
                new_callable=AsyncMock,
                return_value={"_id": "cam1", "name": "Cam"},
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_resolve.resolve_camera_uid",
                new_callable=AsyncMock,
                return_value="ip_1",
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_resolve.camera_display_name",
                return_value="Cam",
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_resolve._iter_playable_sessions",
                new_callable=AsyncMock,
                return_value=sessions,
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_resolve._utc_now",
                return_value=now,
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_buffer.resolve_buffer_offset",
                return_value=buf_offset,
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_buffer.get_buffer_window",
                return_value=buf_window,
            )
        )
        stack.enter_context(
            patch(
                "app.services.instant_replay_config.permanent_recording_segment_seconds",
                return_value=300.0,
            )
        )
        return stack

    async def test_recent_30s_prefers_buffer_over_active_recording(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "sess_active",
                "folderId": "ip_1",
                "started": now - timedelta(hours=1),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        oldest = now - timedelta(seconds=180)
        newest = now
        buf_offset = {
            "ok": True,
            "sourceType": "instant_replay_buffer",
            "sessionId": None,
            "playlistUrl": "/api/playback/instant-replay-buffer/ip_1/media/index.m3u8",
            "offsetSeconds": 150.0,
            "oldestAvailableAt": oldest.isoformat(),
            "newestAvailableAt": newest.isoformat(),
            "status": "buffering",
        }
        buf_window = {
            "active": True,
            "oldestAvailableAt": oldest.isoformat(),
            "newestAvailableAt": newest.isoformat(),
            "segmentCount": 90,
        }
        with self._common_patches(now, sessions, buf_offset, buf_window):
            r = await resolve_instant_replay("cam1", seconds_ago=30)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sourceType"], "instant_replay_buffer")
        self.assertFalse(r["permanentReliable"])
        self.assertIsNotNone(r.get("resolvedAt"))

    async def test_10s_30s_60s_resolve_via_buffer(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "sess_active",
                "folderId": "ip_1",
                "started": now - timedelta(hours=1),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        oldest = now - timedelta(seconds=200)
        newest = now
        for secs in (10, 30, 60):
            target = now - timedelta(seconds=secs)
            offset = (target - oldest).total_seconds()
            buf_offset = {
                "ok": True,
                "sourceType": "instant_replay_buffer",
                "playlistUrl": "/api/playback/instant-replay-buffer/ip_1/media/index.m3u8",
                "offsetSeconds": offset,
                "oldestAvailableAt": oldest.isoformat(),
                "newestAvailableAt": newest.isoformat(),
                "status": "buffering",
            }
            buf_window = {
                "active": True,
                "oldestAvailableAt": oldest.isoformat(),
                "newestAvailableAt": newest.isoformat(),
                "segmentCount": 100,
            }
            with self._common_patches(now, sessions, buf_offset, buf_window):
                r = await resolve_instant_replay("cam1", seconds_ago=secs)
            self.assertTrue(r["ok"], msg=f"{secs}s failed")
            self.assertEqual(r["sourceType"], "instant_replay_buffer")

    async def test_5min_uses_permanent_when_reliable(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "sess_active",
                "folderId": "ip_1",
                "started": now - timedelta(hours=2),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        # Buffer only has 90s — not enough for 5 min
        oldest = now - timedelta(seconds=90)
        newest = now
        buf_window = {
            "active": True,
            "oldestAvailableAt": oldest.isoformat(),
            "newestAvailableAt": newest.isoformat(),
            "segmentCount": 45,
        }
        with self._common_patches(now, sessions, None, buf_window):
            r = await resolve_instant_replay("cam1", seconds_ago=300)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sourceType"], "recording")
        self.assertEqual(r["sessionId"], "sess_active")
        self.assertTrue(r["permanentReliable"])

    async def test_recent_without_buffer_returns_no_footage_not_coarse_permanent(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        sessions = [
            {
                "sessionId": "sess_active",
                "folderId": "ip_1",
                "started": now - timedelta(hours=1),
                "stopped": now,
                "status": "recording",
                "sourceType": "recording",
                "sessionDir": MagicMock(),
                "doc": {},
            }
        ]
        buf_window = {
            "active": False,
            "oldestAvailableAt": None,
            "newestAvailableAt": None,
            "segmentCount": 0,
        }
        with self._common_patches(now, sessions, None, buf_window):
            r = await resolve_instant_replay("cam1", seconds_ago=30)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_footage")

    async def test_unavailable_history_not_fabricated(self):
        now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        oldest = now - timedelta(seconds=40)
        newest = now
        buf_offset = None  # 5 min not in buffer
        buf_window = {
            "active": True,
            "oldestAvailableAt": oldest.isoformat(),
            "newestAvailableAt": newest.isoformat(),
            "segmentCount": 20,
        }
        with self._common_patches(now, [], buf_offset, buf_window):
            r = await resolve_instant_replay("cam1", seconds_ago=300)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "no_footage")


class TestLeaseDoesNotSuppressForRecording(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.get_camera_by_ref", new_callable=AsyncMock)
    @patch("app.routes.playback.resolve_camera_uid", new_callable=AsyncMock, return_value="ip_1")
    @patch("app.services.video_recording.is_camera_recording", new_callable=AsyncMock, return_value=True)
    @patch(
        "app.services.instant_replay_buffer.acquire_buffer_lease",
        new_callable=AsyncMock,
    )
    @patch(
        "app.routes.playback._parse_json_body",
        new_callable=AsyncMock,
        return_value={"cameraId": "cam1"},
    )
    async def test_lease_starts_buffer_while_permanently_recording(
        self, _body, mock_acquire, _rec, _uid, mock_cam, _pb, _acl
    ):
        from app.routes.playback import instant_replay_lease_acquire_endpoint

        mock_cam.return_value = {"_id": "cam1", "camera_uid": "ip_1"}
        mock_acquire.return_value = {
            "ok": True,
            "leaseId": "L1",
            "cameraUid": "ip_1",
            "playlistUrl": "/api/playback/instant-replay-buffer/ip_1/media/index.m3u8",
        }
        request = make_mocked_request("POST", "/api/playback/instant-replay/lease")
        response = await instant_replay_lease_acquire_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertTrue(body["ok"])
        self.assertTrue(body["bufferStarted"])
        self.assertTrue(body["permanentRecordingActive"])
        mock_acquire.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
