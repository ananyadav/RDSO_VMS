"""RDSO 18.1.23 — audit trail coverage gaps (display reset, PTZ config, filters)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.audit import list_audit_logs_endpoint
from app.routes.events import display_reset_event_endpoint
from app.services.audit_service import (
    ACTION_EDGE_BACKFILL_STARTED,
    ACTION_EVENT_DISPLAY_RESET,
    ACTION_PTZ_PRESET_SET,
    ACTION_SYSTEM_TIME_ACTION,
    ACTION_USER_DELETED,
    query_audit_logs,
    sanitize_metadata,
)


CAMERA_ID = "507f1f77bcf86cd799439011"
EVENT_ID = "507f1f77bcf86cd799439013"

SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
EVENTS_OP = {
    "_id": "o2",
    "name": "evt",
    "role": "Operator",
    "permissions": ["Events", "Live View"],
}


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


class TestAuditConstantsAndRedaction(unittest.TestCase):
    def test_new_action_constants(self):
        self.assertEqual(ACTION_USER_DELETED, "USER_DELETED")
        self.assertEqual(ACTION_EVENT_DISPLAY_RESET, "EVENT_DISPLAY_RESET")
        self.assertEqual(ACTION_PTZ_PRESET_SET, "PTZ_PRESET_SET")
        self.assertEqual(ACTION_SYSTEM_TIME_ACTION, "SYSTEM_TIME_ACTION")
        self.assertEqual(ACTION_EDGE_BACKFILL_STARTED, "EDGE_BACKFILL_STARTED")

    def test_secrets_still_redacted_in_metadata(self):
        meta = sanitize_metadata(
            {
                "password": "x",
                "main_rtsp_url": "rtsp://admin:secret@10.0.0.1/stream",
                "camera_id": CAMERA_ID,
                "token": "sess",
            }
        )
        self.assertEqual(meta["password"], "[REDACTED]")
        self.assertEqual(meta["token"], "[REDACTED]")
        self.assertNotIn("secret", str(meta["main_rtsp_url"]))
        self.assertEqual(meta["camera_id"], CAMERA_ID)


class TestDisplayResetAudited(unittest.IsolatedAsyncioTestCase):
    async def test_display_reset_writes_audit_without_ack(self):
        reset = {
            "id": EVENT_ID,
            "camera_id": CAMERA_ID,
            "source_type": "motion",
            "severity": "warning",
            "title": "Motion",
            "acknowledged": False,
            "status": "open",
            "metadata": {"display_reset": True},
        }
        with patch(
            "app.routes.events.display_reset_event",
            new_callable=AsyncMock,
            return_value=reset,
        ), patch(
            "app.routes.events.write_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit:
            response = await display_reset_event_endpoint(
                _request("POST", f"/api/events/{EVENT_ID}/display-reset", EVENTS_OP, {"id": EVENT_ID}),
            )
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertFalse(body["acknowledged"])
        self.assertEqual(audit.await_args.kwargs["action"], ACTION_EVENT_DISPLAY_RESET)
        self.assertEqual(audit.await_args.kwargs["metadata"]["camera_id"], CAMERA_ID)


class TestAuditQueryCameraFilter(unittest.IsolatedAsyncioTestCase):
    async def test_camera_id_builds_or_clause(self):
        class _EmptyCursor:
            def sort(self, *a, **k):
                return self

            def skip(self, *a, **k):
                return self

            def limit(self, *a, **k):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        with patch("app.services.audit_service.AUDIT_COLLECTION") as col:
            col.count_documents = AsyncMock(return_value=0)
            col.find.return_value = _EmptyCursor()
            await query_audit_logs(camera_id=CAMERA_ID, limit=10, offset=0)
            q = col.count_documents.await_args.args[0]
        self.assertIn("$or", q)
        self.assertEqual(q["$or"][0]["resource_id"], CAMERA_ID)
        self.assertEqual(q["$or"][1]["metadata.camera_id"], CAMERA_ID)

    async def test_api_passes_camera_id(self):
        with patch(
            "app.routes.audit.query_audit_logs",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ) as q:
            request = make_mocked_request("GET", f"/api/audit-logs?camera_id={CAMERA_ID}")
            request["auth_user"] = SUPER
            response = await list_audit_logs_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(q.await_args.kwargs.get("camera_id"), CAMERA_ID)

    async def test_admin_still_forbidden_list(self):
        response = await list_audit_logs_endpoint(_request("GET", "/api/audit-logs", ADMIN))
        self.assertEqual(response.status, 403)


class TestPtzPresetAudited(unittest.IsolatedAsyncioTestCase):
    async def test_preset_set_audits_on_success(self):
        from app.routes.ptz import ptz_preset_set

        cam = {"_id": CAMERA_ID, "ptz": True, "is_active": True}
        req = _request("PUT", f"/api/ptz/{CAMERA_ID}/presets/1", EVENTS_OP, {"cameraId": CAMERA_ID, "presetId": "1"})

        async def _json():
            return {"name": "Gate"}

        req.json = _json  # type: ignore[method-assign]
        with patch(
            "app.routes.ptz._require_live_camera",
            new_callable=AsyncMock,
            return_value=(cam, None),
        ), patch(
            "app.routes.ptz.set_preset",
            new_callable=AsyncMock,
            return_value={"ok": True},
        ), patch(
            "app.routes.ptz.write_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit:
            response = await ptz_preset_set(req)
        self.assertEqual(response.status, 200)
        self.assertEqual(audit.await_args.kwargs["action"], ACTION_PTZ_PRESET_SET)
        self.assertEqual(audit.await_args.kwargs["resource_id"], CAMERA_ID)
