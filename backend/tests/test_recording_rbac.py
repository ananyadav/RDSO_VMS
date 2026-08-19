import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.core.access_control import (
    PERMISSION_LIVE_VIEW,
    PERMISSION_RECORDING_VIEW,
    deny_unless_playback_permission,
    has_recording_view,
)
from app.core.startup_state import _recording_health_snapshot
from app.routes.playback import playback_media_endpoint, playback_search_endpoint
from app.routes.recording import (
    delete_session_endpoint,
    download_session_endpoint,
    export_session_endpoint,
    get_session_endpoint,
    list_all_sessions_endpoint,
    maybe_start_recording_engine,
    recording_health_endpoint,
    retention_run_endpoint,
    start_recording_endpoint,
    storage_settings_update_endpoint,
    update_recording_schedule_endpoint,
)
from app.services.recording_config import is_recording_engine_enabled
from app.services.video_recording import start_camera_recording


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": [PERMISSION_LIVE_VIEW]}
OPERATOR_VIEW = {
    "_id": "o2",
    "name": "recop",
    "role": "Operator",
    "permissions": [PERMISSION_LIVE_VIEW, PERMISSION_RECORDING_VIEW],
    "cameraAccess": {
        "allowedCameraGroups": [],
        "allowedCameraUids": ["ip_cam_a"],
    },
}
VIEWER = {"_id": "v1", "name": "watch", "role": "Viewer", "permissions": ["Live View"]}
SESSION_ID = "507f191e810c19729de860ea"
SESSION = {
    "id": SESSION_ID,
    "camera_id": "cam_a",
    "status": "stopped",
    "started_at": "2026-08-01T10:00:00+00:00",
    "stopped_at": "2026-08-01T10:05:00+00:00",
}


class TestRecordingViewPermission(unittest.IsolatedAsyncioTestCase):
    async def test_admin_and_super_admin_have_view(self):
        self.assertTrue(has_recording_view(ADMIN))
        self.assertTrue(has_recording_view(SUPER))
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", ADMIN))
        self.assertIsNone(denied)
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", SUPER))
        self.assertIsNone(denied)

    async def test_operator_without_recording_view_forbidden(self):
        self.assertFalse(has_recording_view(OPERATOR))
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", OPERATOR))
        self.assertIsNotNone(denied)
        self.assertEqual(denied.status, 403)

    async def test_operator_with_recording_view_allowed(self):
        self.assertTrue(has_recording_view(OPERATOR_VIEW))
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", OPERATOR_VIEW))
        self.assertIsNone(denied)

    async def test_viewer_not_auto_granted(self):
        self.assertFalse(has_recording_view(VIEWER))
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", VIEWER))
        self.assertEqual(denied.status, 403)

    async def test_unauthenticated_401(self):
        denied = await deny_unless_playback_permission(_request("GET", "/api/playback/search", None))
        self.assertEqual(denied.status, 401)


class TestRecordingMutationsSuperAdminOnly(unittest.IsolatedAsyncioTestCase):
    async def test_admin_delete_403(self):
        response = await delete_session_endpoint(
            _request("DELETE", f"/api/recordings/sessions/{SESSION_ID}", ADMIN, {"sessionId": SESSION_ID})
        )
        self.assertEqual(response.status, 403)

    async def test_operator_delete_403(self):
        response = await delete_session_endpoint(
            _request("DELETE", f"/api/recordings/sessions/{SESSION_ID}", OPERATOR_VIEW, {"sessionId": SESSION_ID})
        )
        self.assertEqual(response.status, 403)

    async def test_admin_download_export_403(self):
        for handler, path in (
            (download_session_endpoint, f"/api/recordings/sessions/{SESSION_ID}/download"),
            (export_session_endpoint, f"/api/recordings/sessions/{SESSION_ID}/export"),
        ):
            response = await handler(
                _request("GET", path, ADMIN, {"sessionId": SESSION_ID})
            )
            self.assertEqual(response.status, 403, path)

    async def test_operator_download_export_403(self):
        response = await download_session_endpoint(
            _request("GET", f"/api/recordings/sessions/{SESSION_ID}/download", OPERATOR_VIEW, {"sessionId": SESSION_ID})
        )
        self.assertEqual(response.status, 403)
        response = await export_session_endpoint(
            _request("GET", f"/api/recordings/sessions/{SESSION_ID}/export", OPERATOR_VIEW, {"sessionId": SESSION_ID})
        )
        self.assertEqual(response.status, 403)

    async def test_admin_config_403(self):
        request = _request("POST", "/api/recordings/schedule", ADMIN)

        async def _json():
            return {"schedule": {}}

        request.json = _json  # type: ignore[method-assign]
        response = await update_recording_schedule_endpoint(request)
        self.assertEqual(response.status, 403)

        request = _request("PUT", "/api/storage/settings", ADMIN)

        async def _body():
            return {"retention_days": 7}

        request.json = _body  # type: ignore[method-assign]
        response = await storage_settings_update_endpoint(request)
        self.assertEqual(response.status, 403)

        response = await retention_run_endpoint(_request("POST", "/api/storage/retention/run", ADMIN))
        self.assertEqual(response.status, 403)

    async def test_operator_config_403(self):
        request = _request("POST", "/api/recordings/schedule", OPERATOR_VIEW)

        async def _json():
            return {"schedule": {}}

        request.json = _json  # type: ignore[method-assign]
        response = await update_recording_schedule_endpoint(request)
        self.assertEqual(response.status, 403)


class TestSuperAdminDeleteAudit(unittest.IsolatedAsyncioTestCase):
    async def test_delete_creates_recording_deleted_audit(self):
        raw = {"_id": SESSION_ID, **SESSION}
        request = _request("DELETE", f"/api/recordings/sessions/{SESSION_ID}", SUPER, {"sessionId": SESSION_ID})
        with patch(
            "app.routes.recording.recording_sessions_collection"
        ) as coll, patch(
            "app.routes.recording.get_recording_session", new_callable=AsyncMock, return_value=dict(SESSION)
        ), patch(
            "app.routes.recording.update_recording_session", new_callable=AsyncMock
        ), patch(
            "app.routes.recording.get_camera_by_ref", new_callable=AsyncMock, return_value={"name": "Gate"}
        ), patch(
            "app.routes.recording.commit_critical_audit", new_callable=AsyncMock, return_value=True
        ) as audit, patch(
            "app.routes.recording.delete_recording_session_files", new_callable=AsyncMock, return_value=12
        ):
            coll.find_one = AsyncMock(return_value=raw)
            coll.replace_one = AsyncMock()
            response = await delete_session_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["status"], "deleted")
        kwargs = audit.await_args.kwargs
        self.assertEqual(kwargs["action"], "RECORDING_DELETED")
        self.assertEqual(kwargs["metadata"]["camera_id"], "cam_a")
        self.assertEqual(kwargs["metadata"]["path"], f"cam_a/sessions/{SESSION_ID}")
        self.assertNotIn("password", json.dumps(kwargs["metadata"]))

    async def test_delete_rolls_back_when_audit_fails(self):
        raw = {"_id": SESSION_ID, **SESSION}
        request = _request("DELETE", f"/api/recordings/sessions/{SESSION_ID}", SUPER, {"sessionId": SESSION_ID})
        with patch(
            "app.routes.recording.recording_sessions_collection"
        ) as coll, patch(
            "app.routes.recording.get_recording_session", new_callable=AsyncMock, return_value=dict(SESSION)
        ), patch(
            "app.routes.recording.update_recording_session", new_callable=AsyncMock
        ), patch(
            "app.routes.recording.get_camera_by_ref", new_callable=AsyncMock, return_value={"name": "Gate"}
        ), patch(
            "app.routes.recording.commit_critical_audit", new_callable=AsyncMock, return_value=False
        ), patch(
            "app.routes.recording.delete_recording_session_files", new_callable=AsyncMock
        ) as delete_files:
            coll.find_one = AsyncMock(return_value=raw)
            response = await delete_session_endpoint(request)
        self.assertEqual(response.status, 500)
        delete_files.assert_not_awaited()


class TestSessionCameraAcl(unittest.IsolatedAsyncioTestCase):
    async def test_get_session_forbidden_for_unassigned_camera(self):
        request = _request("GET", "/api/recordings/sessions/sessB", OPERATOR_VIEW, {"sessionId": "sessB"})
        other = dict(SESSION, id="sessB", camera_id="cam_b")
        with patch(
            "app.routes.recording.get_recording_session", new_callable=AsyncMock, return_value=other
        ), patch(
            "app.core.access_control.get_camera_by_ref", new_callable=AsyncMock
        ) as by_ref:
            by_ref.return_value = {"_id": "cam_b", "camera_uid": "ip_cam_b", "camera_group": "site_b"}
            response = await get_session_endpoint(request)
        self.assertEqual(response.status, 403)

    async def test_list_sessions_filters_unauthorized_cameras(self):
        request = _request("GET", "/api/recordings/sessions", OPERATOR_VIEW)
        sessions = [
            dict(SESSION, id="a", camera_id="cam_a"),
            dict(SESSION, id="b", camera_id="cam_b"),
        ]
        with patch(
            "app.routes.recording.list_recording_sessions", new_callable=AsyncMock, return_value=sessions
        ), patch(
            "app.routes.recording.get_camera_by_ref", new_callable=AsyncMock
        ) as by_ref:
            async def _cam(ref):
                if ref == "cam_a":
                    return {"_id": "cam_a", "camera_uid": "ip_cam_a", "camera_group": "site_a"}
                return {"_id": "cam_b", "camera_uid": "ip_cam_b", "camera_group": "site_b"}

            by_ref.side_effect = _cam
            response = await list_all_sessions_endpoint(request)
        self.assertEqual(response.status, 200)
        ids = [s["id"] for s in json.loads(response.text)["sessions"]]
        self.assertEqual(ids, ["a"])
        self.assertNotIn("b", ids)

    async def test_playback_search_camera_acl(self):
        request = make_mocked_request("GET", "/api/playback/search?cameraId=cam_b&date=2026-08-01")
        request["auth_user"] = OPERATOR_VIEW
        with patch(
            "app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock
        ) as cam_deny:
            from aiohttp import web

            cam_deny.return_value = web.json_response({"error": "Camera access denied"}, status=403)
            response = await playback_search_endpoint(request)
        self.assertEqual(response.status, 403)


class TestDisabledRecordingEngine(unittest.IsolatedAsyncioTestCase):
    async def test_env_flag_defaults_false(self):
        with patch.dict(os.environ, {"RECORDING_ENABLED": "false"}, clear=False):
            self.assertFalse(is_recording_engine_enabled())

    async def test_start_refuses_without_calling_ffmpeg(self):
        request = _request("POST", "/api/recordings/cam1/start", SUPER, {"cameraId": "cam1"})
        with patch("app.routes.recording.is_recording_engine_enabled", return_value=False), patch(
            "app.routes.recording.start_camera_recording", new_callable=AsyncMock
        ) as start:
            response = await start_recording_endpoint(request)
        self.assertEqual(response.status, 409)
        start.assert_not_awaited()
        body = json.loads(response.text)
        self.assertFalse(body["enabled"])
        self.assertFalse(body["recordingActive"])

    async def test_start_camera_recording_raises_when_disabled(self):
        with patch("app.services.recording_config.is_recording_engine_enabled", return_value=False):
            with self.assertRaises(Exception) as ctx:
                await start_camera_recording("cam1")
        self.assertEqual(type(ctx.exception).__name__, "RecordingEngineDisabled")
        self.assertIn("disabled", str(ctx.exception).lower())

    async def test_engine_jobs_do_not_start_when_disabled(self):
        with patch("app.routes.recording.is_recording_engine_enabled", return_value=False), patch(
            "app.routes.recording.finalize_orphaned_recording_sessions", new_callable=AsyncMock
        ) as orphan, patch(
            "app.routes.recording.backfill_all_session_stats_from_disk", new_callable=AsyncMock
        ) as backfill, patch(
            "app.routes.recording.run_retention_pass", new_callable=AsyncMock
        ) as retention, patch(
            "app.routes.recording.asyncio.create_task"
        ) as create_task:
            started = await maybe_start_recording_engine()
        self.assertFalse(started)
        orphan.assert_not_awaited()
        backfill.assert_not_awaited()
        retention.assert_not_awaited()
        create_task.assert_not_called()

    async def test_health_reports_disabled_not_broken(self):
        snap = _recording_health_snapshot()
        self.assertIn("enabled", snap)
        self.assertIn("recordingActive", snap)
        with patch("app.routes.recording.is_recording_engine_enabled", return_value=False):
            response = await recording_health_endpoint(_request("GET", "/api/recordings/health", SUPER))
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertFalse(body["enabled"])
        self.assertFalse(body["recordingActive"])
        self.assertEqual(body["status"], "disabled")

    async def test_playback_still_authorized_when_disabled(self):
        request = make_mocked_request(
            "GET",
            "/api/playback/cam_a/sess1/media/index.m3u8",
            match_info={"cameraId": "cam_a", "sessionId": "sess1", "filename": "index.m3u8"},
        )
        request["auth_user"] = ADMIN
        with patch(
            "app.routes.playback.deny_unless_camera_access", new_callable=AsyncMock, return_value=None
        ), patch(
            "app.routes.playback.build_recording_media_response", new_callable=AsyncMock
        ) as build:
            from aiohttp import web

            build.return_value = web.Response(text="ok", status=200)
            response = await playback_media_endpoint(request)
        self.assertEqual(response.status, 200)

    async def test_retention_run_refused_when_disabled(self):
        with patch("app.routes.recording.is_recording_engine_enabled", return_value=False), patch(
            "app.routes.recording.run_retention_pass", new_callable=AsyncMock
        ) as retention:
            response = await retention_run_endpoint(_request("POST", "/api/storage/retention/run", SUPER))
        self.assertEqual(response.status, 409)
        retention.assert_not_awaited()


class TestSuperAdminDownload(unittest.IsolatedAsyncioTestCase):
    async def test_super_admin_download_zip(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "index.m3u8").write_text("#EXTM3U\n")
        (tmp / "seg_00001.ts").write_bytes(b"ts")
        request = _request(
            "GET", f"/api/recordings/sessions/{SESSION_ID}/download", SUPER, {"sessionId": SESSION_ID}
        )
        with patch(
            "app.routes.recording.get_recording_session", new_callable=AsyncMock, return_value=dict(SESSION)
        ), patch(
            "app.routes.recording.resolve_session_dir", new_callable=AsyncMock, return_value=tmp
        ), patch(
            "app.routes.recording.write_audit", new_callable=AsyncMock, return_value=True
        ):
            response = await download_session_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        self.assertIn("attachment", response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
