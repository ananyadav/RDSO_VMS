import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.core.access_control import deny_unless_super_admin, has_permission, PERMISSION_LIVE_VIEW
from app.routes.audit import list_audit_logs_endpoint
from app.routes.auth import login_endpoint, logout_endpoint
from app.routes.cameras import add_camera_endpoint, delete_camera_endpoint, update_camera_endpoint
from app.routes.go2rtc import go2rtc_diagnostics, go2rtc_workers_rebalance
from app.routes.locations import post_site_endpoint
from app.routes.ptz import ptz_stop_handler
from app.routes.sessions import list_sessions_endpoint, revoke_sessions_endpoint
from app.routes.users import add_user_endpoint, get_users_list, update_user_endpoint
from app.services.session_service import _public_session


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": [PERMISSION_LIVE_VIEW]}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}


class TestSuperAdminOnlyEndpoints(unittest.IsolatedAsyncioTestCase):
    async def test_audit_forbidden_for_admin_and_operator(self):
        for user in (ADMIN, OPERATOR):
            response = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", user))
            self.assertEqual(response.status, 403, user["role"])
            body = json.loads(response.text)
            self.assertEqual(body["error"], "Forbidden")
            self.assertNotIn("SUPER_ADMIN", body["error"])

    async def test_audit_unauthenticated_401(self):
        response = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", None))
        self.assertEqual(response.status, 401)

    async def test_audit_allowed_for_super_admin(self):
        with patch(
            "app.routes.audit.query_audit_logs",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ):
            response = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", SUPER))
        self.assertEqual(response.status, 200)

    async def test_sessions_forbidden_for_admin(self):
        response = await list_sessions_endpoint(_request("GET", "/api/sessions", ADMIN))
        self.assertEqual(response.status, 403)

    async def test_revoke_forbidden_for_admin_and_operator(self):
        for user in (ADMIN, OPERATOR):
            denied = await deny_unless_super_admin(_request("POST", "/api/sessions/revoke", user))
            self.assertIsNotNone(denied)
            self.assertEqual(denied.status, 403)

    async def test_revoke_super_admin_ok(self):
        request = _request("POST", "/api/sessions/revoke", SUPER)

        async def _json():
            return {"user_id": "o1"}

        request.json = _json  # type: ignore[method-assign]
        with patch("app.routes.sessions.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.sessions.get_user_by_id", new_callable=AsyncMock, return_value=OPERATOR
        ), patch(
            "app.routes.sessions.revoke_sessions_for_user_tracked",
            new_callable=AsyncMock,
            return_value=(2, ["sid1"]),
        ), patch(
            "app.routes.sessions.commit_critical_audit", new_callable=AsyncMock, return_value=True
        ):
            read_body.return_value = ({"user_id": "o1"}, None)
            response = await revoke_sessions_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["revoked"], 2)

    async def test_user_list_hides_super_admin_from_admin(self):
        listed = [
            {"id": "s1", "name": "root", "role": "SUPER_ADMIN"},
            {"id": "o1", "name": "camop", "role": "Operator"},
            {"id": "a2", "name": "other-admin", "role": "Admin"},
        ]
        with patch("app.routes.users.get_users", new_callable=AsyncMock, return_value=listed):
            response = await get_users_list(_request("GET", "/api/users", ADMIN))
        self.assertEqual(response.status, 200)
        names = {u["name"] for u in json.loads(response.text)}
        self.assertNotIn("root", names)
        self.assertIn("camop", names)
        self.assertIn("other-admin", names)

        with patch("app.routes.users.get_users", new_callable=AsyncMock, return_value=listed):
            response = await get_users_list(_request("GET", "/api/users", SUPER))
        self.assertEqual(response.status, 200)
        names = {u["name"] for u in json.loads(response.text)}
        self.assertIn("root", names)

    async def test_admin_cannot_create_or_modify_privileged_users(self):
        with patch("app.routes.users.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.users.write_audit", new_callable=AsyncMock
        ):
            read_body.return_value = ({"name": "boss", "password": "x", "role": "Admin"}, None)
            created = await add_user_endpoint(_request("POST", "/api/users", ADMIN))
        self.assertEqual(created.status, 403)
        self.assertEqual(json.loads(created.text)["error"], "Forbidden")

        with patch("app.routes.users.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.users.get_user_by_id", new_callable=AsyncMock, return_value=SUPER
        ), patch("app.routes.users.write_audit", new_callable=AsyncMock):
            read_body.return_value = ({"status": "Disabled"}, None)
            updated = await update_user_endpoint(_request("PUT", "/api/users/s1", ADMIN))
        self.assertEqual(updated.status, 404)
        self.assertEqual(json.loads(updated.text)["error"], "User not found")
        self.assertNotIn("SUPER_ADMIN", json.loads(updated.text)["error"])

    async def test_operator_cannot_list_users(self):
        response = await get_users_list(_request("GET", "/api/users", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_operator_cannot_manage_cameras_or_locations(self):
        cam = await add_camera_endpoint(_request("POST", "/api/cameras", OPERATOR))
        self.assertEqual(cam.status, 403)
        loc = await post_site_endpoint(_request("POST", "/api/locations/sites", OPERATOR))
        self.assertEqual(loc.status, 403)
        deleted = await delete_camera_endpoint(_request("DELETE", "/api/cameras/x", OPERATOR))
        self.assertEqual(deleted.status, 403)

    async def test_operator_keeps_live_view_permission(self):
        self.assertTrue(has_permission(OPERATOR, PERMISSION_LIVE_VIEW))
        self.assertFalse(has_permission(OPERATOR, "Cameras"))

    async def test_login_success_and_failure_audit(self):
        with patch("app.routes.auth.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.auth.handle_login", new_callable=AsyncMock, return_value=({"error": "Invalid credentials"}, 401)
        ), patch("app.routes.auth.write_audit", new_callable=AsyncMock) as audit:
            read_body.return_value = ({"name": "nobody", "password": "bad"}, None)
            response = await login_endpoint(_request("POST", "/api/login", None))
        self.assertEqual(response.status, 401)
        body = json.loads(response.text)
        self.assertEqual(body["error"], "Invalid credentials")
        self.assertEqual(audit.await_args.kwargs["action"], "LOGIN_FAILED")
        self.assertNotIn("password", str(audit.await_args.kwargs.get("metadata")))

        with patch("app.routes.auth.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.auth.handle_login",
            new_callable=AsyncMock,
            return_value=({"id": "a1", "name": "ops", "role": "Admin"}, 200),
        ), patch("app.routes.auth.create_session", new_callable=AsyncMock, return_value="opaque-token"), patch(
            "app.routes.auth.attach_session_cookie"
        ), patch(
            "app.routes.auth.write_audit", new_callable=AsyncMock
        ) as audit:
            read_body.return_value = ({"name": "ops", "password": "secret"}, None)
            response = await login_endpoint(_request("POST", "/api/login", None))
        self.assertEqual(response.status, 200)
        self.assertEqual(audit.await_args.kwargs["action"], "LOGIN_SUCCESS")
        self.assertNotIn("secret", str(audit.await_args.kwargs))
        self.assertNotIn("opaque-token", str(audit.await_args.kwargs))

    async def test_logout_audits(self):
        request = _request("POST", "/api/logout", ADMIN)
        with patch("app.routes.auth.read_session_token", return_value="opaque-token"), patch(
            "app.routes.auth.revoke_session", new_callable=AsyncMock
        ), patch("app.routes.auth.clear_session_cookie"), patch(
            "app.routes.auth.write_audit", new_callable=AsyncMock
        ) as audit:
            response = await logout_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(audit.await_args.kwargs["action"], "LOGOUT")

    async def test_public_session_omits_token(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        public = _public_session(
            {
                "_id": "sess1",
                "user_id": "o1",
                "user_name": "camop",
                "role": "Operator",
                "created_at": now,
                "expires_at": now,
                "last_seen_at": now,
                "ip_address": "10.0.0.8",
                "user_agent": "test",
                "token": "should-never-appear",
                "revoked_at": None,
            }
        )
        self.assertNotIn("token", public)
        self.assertEqual(public["user_name"], "camop")

    async def test_admin_platform_endpoints_forbidden(self):
        diag = await go2rtc_diagnostics(_request("GET", "/api/go2rtc/diagnostics", ADMIN))
        self.assertEqual(diag.status, 403)
        reb = await go2rtc_workers_rebalance(_request("POST", "/api/go2rtc/workers/rebalance", ADMIN))
        self.assertEqual(reb.status, 403)
        sess = await list_sessions_endpoint(_request("GET", "/api/sessions", ADMIN))
        self.assertEqual(sess.status, 403)
        audit = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", ADMIN))
        self.assertEqual(audit.status, 403)

    async def test_super_admin_platform_endpoints_allowed(self):
        with patch("app.routes.go2rtc.get_go2rtc_diagnostics", new_callable=AsyncMock, return_value={"ok": True}):
            diag = await go2rtc_diagnostics(_request("GET", "/api/go2rtc/diagnostics", SUPER))
        self.assertEqual(diag.status, 200)
        with patch(
            "app.routes.go2rtc.rebalance_worker_assignments",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ), patch("app.routes.go2rtc.sync_all_workers", new_callable=AsyncMock, return_value={"ok": True}):
            reb = await go2rtc_workers_rebalance(_request("POST", "/api/go2rtc/workers/rebalance", SUPER))
        self.assertEqual(reb.status, 200)
        with patch(
            "app.routes.audit.query_audit_logs",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ):
            audit = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", SUPER))
        self.assertEqual(audit.status, 200)

    async def test_admin_camera_and_location_mutations_succeed(self):
        cam_req = _request("POST", "/api/cameras", ADMIN)

        async def _cam_json():
            return {"name": "Cam", "ip_address": "10.0.0.1"}

        cam_req.json = _cam_json  # type: ignore[method-assign]
        with patch(
            "app.routes.cameras.handle_add_camera",
            new_callable=AsyncMock,
            return_value=({"id": "c1", "name": "Cam", "ip_address": "10.0.0.1"}, 201),
        ), patch("app.routes.cameras.commit_critical_audit", new_callable=AsyncMock, return_value=True):
            created = await add_camera_endpoint(cam_req)
        self.assertEqual(created.status, 201)

        cam_id = "507f1f77bcf86cd799439011"
        upd_req = _request("PUT", f"/api/cameras/{cam_id}", ADMIN, match_info={"id": cam_id})

        async def _upd_json():
            return {"password": "newpass", "ip_address": "10.0.0.2"}

        upd_req.json = _upd_json  # type: ignore[method-assign]
        with patch(
            "app.routes.cameras.handle_update_camera",
            new_callable=AsyncMock,
            return_value=({"id": cam_id, "name": "Cam", "ip_address": "10.0.0.2"}, 200),
        ), patch(
            "app.core.database.camera_collection.find_one",
            new_callable=AsyncMock,
            return_value={"_id": cam_id, "name": "Cam", "ip_address": "10.0.0.1"},
        ), patch("app.routes.cameras.commit_critical_audit", new_callable=AsyncMock, return_value=True) as audit:
            updated = await update_camera_endpoint(upd_req)
        self.assertEqual(updated.status, 200)
        self.assertTrue(audit.await_args.kwargs.get("success"))
        self.assertIn("camera_password", audit.await_args.kwargs.get("changes") or {})

        loc_req = _request("POST", "/api/locations/sites", ADMIN)

        async def _loc_json():
            return {"name": "Site A"}

        loc_req.json = _loc_json  # type: ignore[method-assign]
        with patch(
            "app.routes.locations.add_site",
            new_callable=AsyncMock,
            return_value={"id": "s1", "name": "Site A"},
        ), patch("app.routes.locations.sync_locations_catalog", new_callable=AsyncMock), patch(
            "app.routes.locations.load_sites", new_callable=AsyncMock, return_value=[]
        ), patch(
            "app.routes.locations._critical_location_audit", new_callable=AsyncMock, return_value=None
        ):
            loc = await post_site_endpoint(loc_req)
        self.assertEqual(loc.status, 201)

        with patch(
            "app.routes.users.read_json_body",
            new_callable=AsyncMock,
            return_value=({"name": "op2", "password": "x", "role": "Operator"}, None),
        ), patch(
            "app.routes.users.handle_add_user",
            new_callable=AsyncMock,
            return_value=({"id": "o2", "name": "op2", "role": "Operator"}, 201),
        ), patch("app.routes.users.commit_critical_audit", new_callable=AsyncMock, return_value=True):
            op_created = await add_user_endpoint(_request("POST", "/api/users", ADMIN))
        self.assertEqual(op_created.status, 201)

    async def test_admin_ptz_stop_succeeds(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=({"id": "c1", "ptz": True}, None),
        ), patch("app.routes.ptz.ptz_stop", new_callable=AsyncMock, return_value={"ok": True}), patch(
            "app.routes.ptz.write_audit", new_callable=AsyncMock
        ):
            req = _request("POST", "/api/ptz/c1/stop", ADMIN, match_info={"cameraId": "c1"})
            stopped = await ptz_stop_handler(req)
        self.assertEqual(stopped.status, 200)

    async def test_camera_delete_rolls_back_when_audit_fails(self):
        cam_id = "507f1f77bcf86cd799439011"
        existing = {"_id": cam_id, "name": "Cam", "ip_address": "10.0.0.1"}
        with patch("app.routes.cameras.require_admin", new_callable=AsyncMock, return_value=ADMIN), patch(
            "app.core.database.camera_collection.find_one", new_callable=AsyncMock, return_value=existing
        ), patch("app.routes.cameras.delete_camera", new_callable=AsyncMock, return_value=True), patch(
            "app.routes.cameras.commit_critical_audit", new_callable=AsyncMock, return_value=False
        ) as audit:
            req = _request("DELETE", f"/api/cameras/{cam_id}", ADMIN, match_info={"id": cam_id})
            response = await delete_camera_endpoint(req)
        self.assertEqual(response.status, 500)
        self.assertIsNotNone(audit.await_args.kwargs.get("compensate"))


if __name__ == "__main__":
    unittest.main()
