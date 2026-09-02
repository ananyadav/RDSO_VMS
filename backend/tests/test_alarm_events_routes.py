import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.core.access_control import PERMISSION_EVENTS, PERMISSION_LIVE_VIEW, has_events_permission
from app.routes.alarm_rules import (
    create_alarm_rule_endpoint,
    delete_alarm_rule_endpoint,
    get_alarm_rule_endpoint,
    list_alarm_rules_endpoint,
    update_alarm_rule_endpoint,
)
from app.routes.events import acknowledge_event_endpoint, get_event_endpoint, list_events_endpoint
from app.services.alarm_rule_service import AlarmRuleValidationError

CAMERA_ID = "507f1f77bcf86cd799439011"
RULE_ID = "507f1f77bcf86cd799439012"
EVENT_ID = "507f1f77bcf86cd799439013"

VALID_RULE = {
    "id": RULE_ID,
    "name": "Cam signal loss",
    "enabled": True,
    "camera_id": CAMERA_ID,
    "trigger": {"source_type": "signal_loss"},
    "actions": ["create_event", "ui_notification"],
    "severity": "warning",
    "cooldown_seconds": 60,
    "created_by": "a1",
    "created_at": "2026-09-01T10:00:00+00:00",
    "updated_at": "2026-09-01T10:00:00+00:00",
}

VALID_EVENT = {
    "id": EVENT_ID,
    "camera_id": CAMERA_ID,
    "camera_uid": "ip_192_168_41_106",
    "rule_id": RULE_ID,
    "source_type": "signal_loss",
    "severity": "warning",
    "title": "Signal loss",
    "message": "Camera offline",
    "occurred_at": "2026-09-01T10:00:00+00:00",
    "status": "open",
    "acknowledged": False,
    "acknowledged_by": None,
    "acknowledged_at": None,
    "actions_triggered": ["create_event", "ui_notification"],
    "ui_notification": True,
    "metadata": {},
}


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": [PERMISSION_LIVE_VIEW]}
EVENTS_OP = {
    "_id": "o2",
    "name": "evt",
    "role": "Operator",
    "permissions": [PERMISSION_EVENTS],
    "cameraAccess": {"allowedCameraGroups": [], "allowedCameraUids": ["ip_192_168_41_106"]},
}
VIEWER = {"_id": "v1", "name": "watch", "role": "Viewer", "permissions": []}


class TestAlarmRuleRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_admin_creates_valid_alarm_rule(self):
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.create_alarm_rule",
            new_callable=AsyncMock,
            return_value=VALID_RULE,
        ), patch(
            "app.routes.alarm_rules.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            read_body.return_value = (
                {
                    "name": "Cam signal loss",
                    "enabled": True,
                    "camera_id": CAMERA_ID,
                    "trigger": {"source_type": "signal_loss"},
                    "actions": ["create_event", "ui_notification"],
                    "severity": "warning",
                    "cooldown_seconds": 60,
                },
                None,
            )
            response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", ADMIN))
        self.assertEqual(response.status, 201)
        body = json.loads(response.text)
        self.assertEqual(body["trigger"]["source_type"], "signal_loss")

    async def test_operator_cannot_create_rule(self):
        response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_viewer_cannot_create_rule(self):
        response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", VIEWER))
        self.assertEqual(response.status, 403)

    async def test_invalid_source_type_rejected(self):
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.create_alarm_rule",
            new_callable=AsyncMock,
            side_effect=AlarmRuleValidationError("Unsupported source_type: ai_person"),
        ):
            read_body.return_value = ({"trigger": {"source_type": "ai_person"}}, None)
            response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", ADMIN))
        self.assertEqual(response.status, 400)

    async def test_invalid_action_rejected(self):
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.create_alarm_rule",
            new_callable=AsyncMock,
            side_effect=AlarmRuleValidationError("Unsupported action: email"),
        ):
            read_body.return_value = ({"actions": ["email"]}, None)
            response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", ADMIN))
        self.assertEqual(response.status, 400)

    async def test_nonexistent_camera_rejected(self):
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.create_alarm_rule",
            new_callable=AsyncMock,
            side_effect=AlarmRuleValidationError("Camera not found"),
        ):
            read_body.return_value = ({"camera_id": CAMERA_ID}, None)
            response = await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", ADMIN))
        self.assertEqual(response.status, 400)

    async def test_update_rule_enable_disable(self):
        updated = {**VALID_RULE, "enabled": False}
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.get_alarm_rule_doc",
            new_callable=AsyncMock,
            return_value={"_id": RULE_ID, **VALID_RULE},
        ), patch(
            "app.routes.alarm_rules.update_alarm_rule",
            new_callable=AsyncMock,
            return_value=updated,
        ), patch(
            "app.routes.alarm_rules.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            read_body.return_value = ({"enabled": False}, None)
            response = await update_alarm_rule_endpoint(
                _request("PUT", f"/api/alarm-rules/{RULE_ID}", ADMIN, {"id": RULE_ID}),
            )
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.text)["enabled"])

    async def test_delete_rule(self):
        with patch(
            "app.routes.alarm_rules.get_alarm_rule_doc",
            new_callable=AsyncMock,
            return_value={"_id": RULE_ID, **VALID_RULE},
        ), patch(
            "app.routes.alarm_rules.delete_alarm_rule",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.routes.alarm_rules.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await delete_alarm_rule_endpoint(
                _request("DELETE", f"/api/alarm-rules/{RULE_ID}", ADMIN, {"id": RULE_ID}),
            )
        self.assertEqual(response.status, 204)

    async def test_events_permission_can_read_rules(self):
        with patch(
            "app.routes.alarm_rules.list_alarm_rules",
            new_callable=AsyncMock,
            return_value={"items": [VALID_RULE], "total": 1, "limit": 100, "offset": 0},
        ):
            response = await list_alarm_rules_endpoint(_request("GET", "/api/alarm-rules", EVENTS_OP))
        self.assertEqual(response.status, 200)

    async def test_operator_without_events_cannot_read_rules(self):
        response = await list_alarm_rules_endpoint(_request("GET", "/api/alarm-rules", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_rule_create_audited(self):
        with patch("app.routes.alarm_rules.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.alarm_rules.create_alarm_rule",
            new_callable=AsyncMock,
            return_value=VALID_RULE,
        ), patch(
            "app.routes.alarm_rules.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit:
            read_body.return_value = (VALID_RULE, None)
            await create_alarm_rule_endpoint(_request("POST", "/api/alarm-rules", ADMIN))
        self.assertEqual(audit.await_args.kwargs["action"], "ALARM_RULE_CREATED")


class TestEventRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_events_permission_required(self):
        response = await list_events_endpoint(_request("GET", "/api/events", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_admin_can_query_events(self):
        with patch(
            "app.routes.events.list_events",
            new_callable=AsyncMock,
            return_value={"items": [VALID_EVENT], "total": 1, "limit": 50, "offset": 0},
        ):
            response = await list_events_endpoint(_request("GET", "/api/events", ADMIN))
        self.assertEqual(response.status, 200)

    async def test_events_operator_has_permission(self):
        self.assertTrue(has_events_permission(EVENTS_OP))

    async def test_get_unauthorized_event_not_found(self):
        with patch(
            "app.routes.events.get_event",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await get_event_endpoint(
                _request("GET", f"/api/events/{EVENT_ID}", EVENTS_OP, {"id": EVENT_ID}),
            )
        self.assertEqual(response.status, 404)

    async def test_acknowledge_event(self):
        acked = {
            **VALID_EVENT,
            "acknowledged": True,
            "acknowledged_by": "o2",
            "acknowledged_at": "2026-09-01T10:05:00+00:00",
            "status": "acknowledged",
        }
        with patch(
            "app.routes.events.get_event",
            new_callable=AsyncMock,
            return_value=VALID_EVENT,
        ), patch(
            "app.routes.events.acknowledge_event",
            new_callable=AsyncMock,
            return_value=acked,
        ), patch(
            "app.routes.events.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await acknowledge_event_endpoint(
                _request("POST", f"/api/events/{EVENT_ID}/acknowledge", EVENTS_OP, {"id": EVENT_ID}),
            )
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertTrue(body["acknowledged"])
        self.assertEqual(body["status"], "acknowledged")

    async def test_acknowledge_audited(self):
        acked = {**VALID_EVENT, "acknowledged": True, "status": "acknowledged"}
        with patch(
            "app.routes.events.get_event",
            new_callable=AsyncMock,
            return_value=VALID_EVENT,
        ), patch(
            "app.routes.events.acknowledge_event",
            new_callable=AsyncMock,
            return_value=acked,
        ), patch(
            "app.routes.events.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit:
            await acknowledge_event_endpoint(
                _request("POST", f"/api/events/{EVENT_ID}/acknowledge", EVENTS_OP, {"id": EVENT_ID}),
            )
        self.assertEqual(audit.await_args.kwargs["action"], "EVENT_ACKNOWLEDGED")

    async def test_list_events_passes_user_for_acl(self):
        with patch(
            "app.routes.events.list_events",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ) as list_mock:
            await list_events_endpoint(_request("GET", "/api/events", EVENTS_OP))
        self.assertEqual(list_mock.await_args.args[0]["_id"], "o2")

    async def test_list_events_ui_notification_filter(self):
        with patch(
            "app.routes.events.list_events",
            new_callable=AsyncMock,
            return_value={"items": [VALID_EVENT], "total": 1, "limit": 50, "offset": 0},
        ) as list_mock:
            request = make_mocked_request("GET", "/api/events?ui_notification=true")
            request["auth_user"] = ADMIN
            response = await list_events_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertTrue(list_mock.await_args.kwargs.get("ui_notification"))
