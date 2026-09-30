"""PTZ route security and move/stop error handling (18.1.11.3)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.core.access_control import PERMISSION_LIVE_VIEW
from app.routes.ptz import (
    _require_live_camera,
    ptz_move,
    ptz_preset_delete,
    ptz_preset_goto,
    ptz_preset_set,
    ptz_presets_list,
    ptz_stop_handler,
    ptz_tour_delete,
    ptz_tour_set,
    ptz_tour_start,
    ptz_tour_stop,
    ptz_tours_list,
    ptz_pattern_delete,
    ptz_pattern_record_start,
    ptz_pattern_record_stop,
    ptz_pattern_set,
    ptz_pattern_start,
    ptz_pattern_stop,
    ptz_patterns_list,
)


def _request(method: str, path: str, user=None, match_info=None, json_body=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user

    async def _json():
        return json_body if json_body is not None else {}

    request.json = _json  # type: ignore[method-assign]
    return request


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": [PERMISSION_LIVE_VIEW]}
NO_LIVE = {"_id": "g1", "name": "guest", "role": "Custom", "permissions": []}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}

PTZ_CAM = {
    "id": "c-ptz",
    "name": "Dome",
    "ptz": True,
    "is_active": True,
    "protocol": "HIKVISION",
    "ip_address": "192.168.1.10",
}
FIXED_CAM = {
    "id": "c-fixed",
    "name": "Fixed",
    "ptz": False,
    "is_active": True,
}


class TestPtzRequireLiveCamera(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_401(self):
        with patch("app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=None):
            cam, err = await _require_live_camera(_request("POST", "/api/ptz/x/move"), "x")
        self.assertIsNone(cam)
        self.assertEqual(err.status, 401)

    async def test_no_live_view_permission_403(self):
        with patch("app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=NO_LIVE):
            cam, err = await _require_live_camera(_request("POST", "/api/ptz/x/move", NO_LIVE), "x")
        self.assertIsNone(cam)
        self.assertEqual(err.status, 403)
        self.assertIn("Live View", json.loads(err.text)["error"])

    async def test_camera_acl_denied(self):
        denied = web.json_response({"error": "Access denied"}, status=403)
        with patch(
            "app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=OPERATOR
        ), patch(
            "app.routes.ptz.deny_unless_camera_access",
            new_callable=AsyncMock,
            return_value=denied,
        ):
            cam, err = await _require_live_camera(
                _request("POST", "/api/ptz/c1/move", OPERATOR), "c1"
            )
        self.assertIsNone(cam)
        self.assertEqual(err.status, 403)

    async def test_non_ptz_camera_400(self):
        with patch(
            "app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=ADMIN
        ), patch(
            "app.routes.ptz.deny_unless_camera_access", new_callable=AsyncMock, return_value=None
        ), patch(
            "app.routes.ptz.get_camera_by_ref", new_callable=AsyncMock, return_value=FIXED_CAM
        ):
            cam, err = await _require_live_camera(
                _request("POST", "/api/ptz/c-fixed/move", ADMIN), "c-fixed"
            )
        self.assertIsNone(cam)
        self.assertEqual(err.status, 400)
        self.assertIn("not marked as PTZ", json.loads(err.text)["error"])

    async def test_ptz_camera_ok(self):
        with patch(
            "app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=OPERATOR
        ), patch(
            "app.routes.ptz.deny_unless_camera_access", new_callable=AsyncMock, return_value=None
        ), patch(
            "app.routes.ptz.get_camera_by_ref", new_callable=AsyncMock, return_value=PTZ_CAM
        ):
            cam, err = await _require_live_camera(
                _request("POST", "/api/ptz/c-ptz/move", OPERATOR), "c-ptz"
            )
        self.assertIsNone(err)
        self.assertEqual(cam["id"], "c-ptz")


class TestPtzMoveStopHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_move_pan_tilt_zoom_directions(self):
        for direction in ("left", "right", "up", "down", "zoom_in", "zoom_out"):
            with patch(
                "app.routes.ptz._require_live_camera",
                new_callable=AsyncMock,
                return_value=(PTZ_CAM, None),
            ), patch(
                "app.routes.ptz.ptz_move_direction",
                new_callable=AsyncMock,
                return_value={"ok": True, "backend": "isapi"},
            ) as move, patch(
                "app.routes.ptz.write_audit", new_callable=AsyncMock
            ), patch(
                "app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=ADMIN
            ):
                req = _request(
                    "POST",
                    f"/api/ptz/c-ptz/move",
                    ADMIN,
                    match_info={"cameraId": "c-ptz"},
                    json_body={"direction": direction, "speed": 2},
                )
                resp = await ptz_move(req)
            self.assertEqual(resp.status, 200, direction)
            move.assert_awaited_once()
            self.assertEqual(move.await_args.args[1], direction)

    async def test_move_protocol_failure_502(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.ptz_move_direction",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "camera unreachable", "backend": "isapi"},
        ):
            req = _request(
                "POST",
                "/api/ptz/c-ptz/move",
                ADMIN,
                match_info={"cameraId": "c-ptz"},
                json_body={"direction": "left", "speed": 2},
            )
            resp = await ptz_move(req)
        self.assertEqual(resp.status, 502)
        body = json.loads(resp.text)
        self.assertFalse(body["ok"])
        self.assertIn("unreachable", body["error"])

    async def test_stop_ok(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.ptz_stop", new_callable=AsyncMock, return_value={"ok": True}
        ), patch("app.routes.ptz.write_audit", new_callable=AsyncMock), patch(
            "app.routes.ptz.get_effective_user", new_callable=AsyncMock, return_value=SUPER
        ):
            resp = await ptz_stop_handler(
                _request("POST", "/api/ptz/c-ptz/stop", SUPER, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(resp.status, 200)

    async def test_stop_protocol_failure_502(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.ptz_stop",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "timeout"},
        ):
            resp = await ptz_stop_handler(
                _request("POST", "/api/ptz/c-ptz/stop", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(resp.status, 502)

    async def test_move_blocked_when_require_fails(self):
        denied = web.json_response({"error": "Camera is not marked as PTZ"}, status=400)
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(None, denied),
        ), patch(
            "app.routes.ptz.ptz_move_direction", new_callable=AsyncMock
        ) as move:
            resp = await ptz_move(
                _request(
                    "POST",
                    "/api/ptz/c-fixed/move",
                    ADMIN,
                    match_info={"cameraId": "c-fixed"},
                    json_body={"direction": "left"},
                )
            )
        self.assertEqual(resp.status, 400)
        move.assert_not_awaited()


class TestPtzPresetTourHandlers(unittest.IsolatedAsyncioTestCase):
    async def test_presets_list_create_goto_delete(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.list_presets",
            new_callable=AsyncMock,
            return_value={"ok": True, "presets": [{"id": 1, "name": "A"}]},
        ):
            listed = await ptz_presets_list(
                _request("GET", "/api/ptz/c-ptz/presets", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(listed.status, 200)
        self.assertEqual(json.loads(listed.text)["presets"][0]["id"], 1)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.set_preset", new_callable=AsyncMock, return_value={"ok": True}
        ):
            saved = await ptz_preset_set(
                _request(
                    "PUT",
                    "/api/ptz/c-ptz/presets/3",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "presetId": "3"},
                    json_body={"name": "Door"},
                )
            )
        self.assertEqual(saved.status, 200)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.goto_preset", new_callable=AsyncMock, return_value={"ok": True}
        ):
            gone = await ptz_preset_goto(
                _request(
                    "POST",
                    "/api/ptz/c-ptz/presets/3/goto",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "presetId": "3"},
                )
            )
        self.assertEqual(gone.status, 200)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.delete_preset", new_callable=AsyncMock, return_value={"ok": True}
        ):
            deleted = await ptz_preset_delete(
                _request(
                    "DELETE",
                    "/api/ptz/c-ptz/presets/3",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "presetId": "3"},
                )
            )
        self.assertEqual(deleted.status, 200)

    async def test_preset_protocol_failure_502(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.goto_preset",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "timeout"},
        ):
            resp = await ptz_preset_goto(
                _request(
                    "POST",
                    "/api/ptz/c-ptz/presets/1/goto",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "presetId": "1"},
                )
            )
        self.assertEqual(resp.status, 502)

    async def test_tours_list_create_start_stop_delete(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.list_tours",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "supported": True,
                "tours": [{"id": 1, "name": "T1", "steps": [{"presetId": 1, "delay": 5}]}],
            },
        ):
            listed = await ptz_tours_list(
                _request("GET", "/api/ptz/c-ptz/tours", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(listed.status, 200)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.set_tour", new_callable=AsyncMock, return_value={"ok": True}
        ) as set_fn:
            saved = await ptz_tour_set(
                _request(
                    "PUT",
                    "/api/ptz/c-ptz/tours/1",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "tourId": "1"},
                    json_body={
                        "name": "Lobby",
                        "steps": [{"presetId": 1, "delay": 5}, {"presetId": 2, "delay": 5}],
                    },
                )
            )
        self.assertEqual(saved.status, 200)
        set_fn.assert_awaited_once()

        for handler, path_suffix in (
            (ptz_tour_start, "start"),
            (ptz_tour_stop, "stop"),
        ):
            with patch(
                "app.routes.ptz._require_live_camera",
                new_callable=AsyncMock,
                return_value=(PTZ_CAM, None),
            ), patch(
                f"app.routes.ptz.{path_suffix}_tour",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ):
                resp = await handler(
                    _request(
                        "POST",
                        f"/api/ptz/c-ptz/tours/1/{path_suffix}",
                        ADMIN,
                        match_info={"cameraId": "c-ptz", "tourId": "1"},
                    )
                )
            self.assertEqual(resp.status, 200, path_suffix)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.delete_tour", new_callable=AsyncMock, return_value={"ok": True}
        ):
            deleted = await ptz_tour_delete(
                _request(
                    "DELETE",
                    "/api/ptz/c-ptz/tours/1",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "tourId": "1"},
                )
            )
        self.assertEqual(deleted.status, 200)

    async def test_tours_unsupported_returns_501(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.list_tours",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "supported": False,
                "tours": [],
                "error": "not supported",
            },
        ):
            resp = await ptz_tours_list(
                _request("GET", "/api/ptz/c-ptz/tours", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(resp.status, 501)
        self.assertFalse(json.loads(resp.text)["supported"])

    async def test_patterns_list_start_stop_record_delete(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.list_patterns",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "supported": True,
                "patterns": [{"id": 1, "name": "Sweep"}],
                "distinct_from_tour_patrol": True,
            },
        ):
            listed = await ptz_patterns_list(
                _request("GET", "/api/ptz/c-ptz/patterns", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(listed.status, 200)
        body = json.loads(listed.text)
        self.assertTrue(body["ok"])
        self.assertTrue(body["distinct_from_tour_patrol"])
        self.assertEqual(body["patterns"][0]["name"], "Sweep")

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.set_pattern", new_callable=AsyncMock, return_value={"ok": True}
        ), patch("app.routes.ptz._audit_ptz", new_callable=AsyncMock) as audit:
            saved = await ptz_pattern_set(
                _request(
                    "PUT",
                    "/api/ptz/c-ptz/patterns/1",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "patternId": "1"},
                    json_body={"name": "Lobby Sweep"},
                )
            )
        self.assertEqual(saved.status, 200)
        audit.assert_awaited()

        for handler, path_suffix, mock_name in (
            (ptz_pattern_start, "start", "start_pattern"),
            (ptz_pattern_stop, "stop", "stop_pattern"),
            (ptz_pattern_record_start, "record-start", "record_pattern_start"),
            (ptz_pattern_record_stop, "record-stop", "record_pattern_stop"),
        ):
            with patch(
                "app.routes.ptz._require_live_camera",
                new_callable=AsyncMock,
                return_value=(PTZ_CAM, None),
            ), patch(
                f"app.routes.ptz.{mock_name}",
                new_callable=AsyncMock,
                return_value={"ok": True},
            ), patch("app.routes.ptz._audit_ptz", new_callable=AsyncMock):
                resp = await handler(
                    _request(
                        "POST",
                        f"/api/ptz/c-ptz/patterns/1/{path_suffix}",
                        ADMIN,
                        match_info={"cameraId": "c-ptz", "patternId": "1"},
                    )
                )
            self.assertEqual(resp.status, 200, path_suffix)

        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.delete_pattern", new_callable=AsyncMock, return_value={"ok": True}
        ), patch("app.routes.ptz._audit_ptz", new_callable=AsyncMock):
            deleted = await ptz_pattern_delete(
                _request(
                    "DELETE",
                    "/api/ptz/c-ptz/patterns/1",
                    ADMIN,
                    match_info={"cameraId": "c-ptz", "patternId": "1"},
                )
            )
        self.assertEqual(deleted.status, 200)

    async def test_patterns_unsupported_returns_501(self):
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(PTZ_CAM, None),
        ), patch(
            "app.routes.ptz.list_patterns",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "supported": False,
                "patterns": [],
                "error": "ONVIF has no standard PTZ Pattern API",
            },
        ):
            resp = await ptz_patterns_list(
                _request("GET", "/api/ptz/c-ptz/patterns", ADMIN, match_info={"cameraId": "c-ptz"})
            )
        self.assertEqual(resp.status, 501)
        self.assertFalse(json.loads(resp.text)["supported"])

    async def test_patterns_acl_blocks_handler(self):
        denied = web.json_response({"error": "Access denied"}, status=403)
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(None, denied),
        ), patch("app.routes.ptz.list_patterns", new_callable=AsyncMock) as listed:
            resp = await ptz_patterns_list(
                _request("GET", "/api/ptz/c1/patterns", OPERATOR, match_info={"cameraId": "c1"})
            )
        self.assertEqual(resp.status, 403)
        listed.assert_not_awaited()

    async def test_preset_acl_blocks_handler(self):
        denied = web.json_response({"error": "Access denied"}, status=403)
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(None, denied),
        ), patch("app.routes.ptz.list_presets", new_callable=AsyncMock) as listed:
            resp = await ptz_presets_list(
                _request("GET", "/api/ptz/c1/presets", OPERATOR, match_info={"cameraId": "c1"})
            )
        self.assertEqual(resp.status, 403)
        listed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
