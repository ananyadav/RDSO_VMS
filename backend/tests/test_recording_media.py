import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request
from bson import ObjectId

from app.routes.playback import playback_media_endpoint
from app.services.recording_media import (
    RECORDING_FILE_NOT_FOUND,
    RECORDING_SESSION_NOT_FOUND,
    RecordingMediaError,
    assert_session_belongs_to_camera,
    resolve_recording_file,
    resolve_session_dir,
    rewrite_playlist_urls,
    validate_filename,
)

SESSION_A = str(ObjectId())
SESSION_B = str(ObjectId())
SESSION_UNKNOWN = str(ObjectId())


class TestRecordingMediaValidation(unittest.TestCase):
    def test_validate_filename_allows_hls(self):
        validate_filename("index.m3u8")
        validate_filename("seg_00001.ts")

    def test_validate_filename_blocks_traversal(self):
        with self.assertRaises(RecordingMediaError) as ctx:
            validate_filename("../secret.ts")
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(RecordingMediaError):
            validate_filename("..")
        with self.assertRaises(RecordingMediaError):
            validate_filename("seg_00001.ts/../../etc/passwd")

    def test_rewrite_playlist_urls(self):
        content = "#EXTM3U\nseg_00001.ts\nseg_00002.ts\n"
        out = rewrite_playlist_urls(content, "cam1", "sess1")
        self.assertIn("/api/playback/cam1/sess1/media/seg_00001.ts", out)
        self.assertIn("/api/playback/cam1/sess1/media/seg_00002.ts", out)


class TestRecordingMediaService(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_recording_file_not_found(self):
        with patch(
            "app.services.recording_media.validate_camera_media_ref",
            new_callable=AsyncMock,
        ), patch(
            "app.services.recording_media.resolve_session_dir",
            new_callable=AsyncMock,
        ) as mock_dir:
            tmp = Path(tempfile.mkdtemp())
            mock_dir.return_value = tmp
            with self.assertRaises(RecordingMediaError) as ctx:
                await resolve_recording_file("cam1", "sess1", "seg_00001.ts")
            self.assertEqual(ctx.exception.status, 404)
            self.assertEqual(ctx.exception.message, RECORDING_FILE_NOT_FOUND)


class TestSessionOwnership(unittest.IsolatedAsyncioTestCase):
    async def test_correct_camera_and_session_media_allowed(self):
        """A. Correct camera + correct session → media allowed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "ip_10_0_0_1" / "sessions" / SESSION_A
            session_dir.mkdir(parents=True)
            (session_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

            session_doc = {
                "_id": ObjectId(SESSION_A),
                "camera_uid": "ip_10_0_0_1",
                "camera_id": "mongo_a",
                "storage_path": f"ip_10_0_0_1/sessions/{SESSION_A}",
            }

            with patch(
                "app.services.recording_media._load_session_identity",
                new_callable=AsyncMock,
                return_value=session_doc,
            ), patch(
                "app.services.recording_media._allowed_camera_identity_keys",
                new_callable=AsyncMock,
                return_value={"ip_10_0_0_1", "mongo_a"},
            ), patch(
                "app.services.recording_media.session_storage_dir",
                side_effect=lambda folder, sid: root / folder / "sessions" / sid,
            ):
                resolved = await resolve_session_dir("ip_10_0_0_1", SESSION_A)
            self.assertEqual(resolved, session_dir)

    async def test_authorized_camera_a_with_session_of_camera_b_returns_404(self):
        """B. Authorized camera A + session belonging to camera B → 404."""
        session_b = {
            "_id": ObjectId(SESSION_B),
            "camera_uid": "ip_10_0_0_2",
            "camera_id": "mongo_b",
            "storage_path": f"ip_10_0_0_2/sessions/{SESSION_B}",
        }
        with patch(
            "app.services.recording_media._load_session_identity",
            new_callable=AsyncMock,
            return_value=session_b,
        ), patch(
            "app.services.recording_media._allowed_camera_identity_keys",
            new_callable=AsyncMock,
            return_value={"ip_10_0_0_1", "mongo_a"},
        ):
            with self.assertRaises(RecordingMediaError) as ctx:
                await resolve_session_dir("ip_10_0_0_1", SESSION_B)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.message, RECORDING_SESSION_NOT_FOUND)

    async def test_assert_session_belongs_mismatch_is_404(self):
        session_b = {
            "_id": ObjectId(SESSION_B),
            "camera_uid": "ip_10_0_0_2",
            "storage_path": f"ip_10_0_0_2/sessions/{SESSION_B}",
        }
        with patch(
            "app.services.recording_media._allowed_camera_identity_keys",
            new_callable=AsyncMock,
            return_value={"ip_10_0_0_1"},
        ):
            with self.assertRaises(RecordingMediaError) as ctx:
                await assert_session_belongs_to_camera("ip_10_0_0_1", session_b)
        self.assertEqual(ctx.exception.status, 404)

    async def test_unknown_session_returns_404(self):
        """D. Unknown session → 404."""
        with patch(
            "app.services.recording_media._load_session_identity",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.recording_media._allowed_camera_identity_keys",
            new_callable=AsyncMock,
            return_value={"ip_10_0_0_1"},
        ), patch(
            "app.services.recording_media.session_storage_dir",
            side_effect=lambda folder, sid: Path("/nonexistent") / folder / "sessions" / sid,
        ):
            with self.assertRaises(RecordingMediaError) as ctx:
                await resolve_session_dir("ip_10_0_0_1", SESSION_UNKNOWN)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.message, RECORDING_SESSION_NOT_FOUND)

    async def test_path_traversal_still_blocked(self):
        """E. Path traversal protection still works."""
        with self.assertRaises(RecordingMediaError) as ctx:
            validate_filename("../other/sessions/x/index.m3u8")
        self.assertIn(ctx.exception.status, (400, 403))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_dir = root / "ip_10_0_0_1" / "sessions" / SESSION_A
            session_dir.mkdir(parents=True)
            (session_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            outside = root / "secret.txt"
            outside.write_text("nope", encoding="utf-8")

            with patch(
                "app.services.recording_media.validate_camera_media_ref",
                new_callable=AsyncMock,
            ), patch(
                "app.services.recording_media.resolve_session_dir",
                new_callable=AsyncMock,
                return_value=session_dir,
            ):
                with self.assertRaises(RecordingMediaError):
                    await resolve_recording_file(
                        "ip_10_0_0_1", SESSION_A, "../secret.txt"
                    )


class TestPlaybackMediaRoute(unittest.IsolatedAsyncioTestCase):
    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.build_recording_media_response", new_callable=AsyncMock)
    async def test_playback_media_route(self, mock_build, _mock_pb, _mock_cam):
        from aiohttp import web

        mock_build.return_value = web.Response(text="ok", status=200)
        request = make_mocked_request(
            "GET",
            "/api/playback/cam1/sess1/media/index.m3u8",
            match_info={
                "cameraId": "cam1",
                "sessionId": "sess1",
                "filename": "index.m3u8",
            },
        )
        response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 200)
        mock_build.assert_awaited_once_with("cam1", "sess1", "index.m3u8", auth_query="")

    @patch("app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.deny_unless_playback_permission", new_callable=AsyncMock, return_value=None)
    @patch("app.routes.playback.build_recording_media_response", new_callable=AsyncMock)
    async def test_playback_media_not_found(self, mock_build, _mock_pb, _mock_cam):
        mock_build.side_effect = RecordingMediaError(RECORDING_FILE_NOT_FOUND, 404)
        request = make_mocked_request(
            "GET",
            "/api/playback/cam1/sess1/media/seg_00001.ts",
            match_info={
                "cameraId": "cam1",
                "sessionId": "sess1",
                "filename": "seg_00001.ts",
            },
        )
        response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 404)

    async def test_user_without_camera_access_still_denied(self):
        """C. User without access to camera → existing ACL behavior remains enforced."""
        from aiohttp import web

        request = make_mocked_request(
            "GET",
            f"/api/playback/ip_10_0_0_1/{SESSION_A}/media/index.m3u8",
            match_info={
                "cameraId": "ip_10_0_0_1",
                "sessionId": SESSION_A,
                "filename": "index.m3u8",
            },
        )
        with patch(
            "app.routes.playback.deny_unless_playback_permission",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routes.playback.deny_unless_camera_access",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "Camera access denied"}, status=403),
        ), patch(
            "app.routes.playback.build_recording_media_response",
            new_callable=AsyncMock,
        ) as build:
            response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 403)
        build.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
