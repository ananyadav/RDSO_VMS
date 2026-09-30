"""RDSO 18.1.15 — reporting utility (alarms / incidents / operator logs)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.reports import (
    alarm_report_endpoint,
    incident_report_endpoint,
    operator_logs_report_endpoint,
)
from app.services.report_service import (
    alarm_report_row,
    event_alarm_state,
    incident_report_row,
    operator_log_row,
    rows_to_csv,
)


CAMERA_ID = "507f1f77bcf86cd799439011"
ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
EVENTS_OP = {
    "_id": "o2",
    "name": "evt",
    "role": "Operator",
    "permissions": ["Events"],
}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": ["Live View"]}


def _request(method: str, path: str, user=None):
    request = make_mocked_request(method, path)
    request["auth_user"] = user
    return request


SAMPLE_EVENT = {
    "id": "507f1f77bcf86cd799439013",
    "camera_id": CAMERA_ID,
    "camera_uid": "ip_192_168_41_90",
    "source_type": "motion",
    "severity": "warning",
    "title": "Motion",
    "message": "motion detected",
    "occurred_at": "2026-09-09T10:00:00+00:00",
    "status": "open",
    "acknowledged": False,
    "metadata": {"signal_restored": True, "recovered_at": "2026-09-09T10:05:00+00:00"},
}


class TestReportRows(unittest.TestCase):
    def test_alarm_state_recovered_and_ack(self):
        self.assertEqual(event_alarm_state(SAMPLE_EVENT), "recovered")
        acked = {**SAMPLE_EVENT, "acknowledged": True, "status": "acknowledged"}
        self.assertEqual(event_alarm_state(acked), "acknowledged")

    def test_csv_escaping_and_fields(self):
        row = alarm_report_row(SAMPLE_EVENT)
        csv_body = rows_to_csv([row], ["event_id", "title", "alarm_state"])
        self.assertIn("alarm_state", csv_body)
        self.assertIn("recovered", csv_body)

    def test_operator_row_uses_sanitized_camera(self):
        row = operator_log_row(
            {
                "id": "1",
                "timestamp": "2026-09-09T10:00:00+00:00",
                "actor_username": "ops",
                "action": "EVENT_DISPLAY_RESET",
                "resource_type": "event",
                "resource_id": "e1",
                "success": True,
                "status": "success",
                "metadata": {"camera_id": CAMERA_ID, "password": "secret", "token": "tok"},
            }
        )
        self.assertEqual(row["camera_id"], CAMERA_ID)
        # password/token not copied into operator row fields
        self.assertNotIn("password", row)
        self.assertNotIn("token", row)


class TestReportRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_alarm_requires_events_permission(self):
        response = await alarm_report_endpoint(_request("GET", "/api/reports/alarms", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_alarm_report_filters_and_pagination(self):
        page = {
            "items": [SAMPLE_EVENT],
            "total": 1,
            "limit": 50,
            "offset": 0,
        }
        with patch(
            "app.services.report_service.list_events",
            new_callable=AsyncMock,
            return_value=page,
        ) as listed:
            request = make_mocked_request(
                "GET",
                f"/api/reports/alarms?camera_id={CAMERA_ID}&source_type=motion&severity=warning&status=open&limit=50&offset=0",
            )
            request["auth_user"] = EVENTS_OP
            response = await alarm_report_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["report"], "alarm")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["alarm_state"], "recovered")
        self.assertEqual(listed.await_args.kwargs["camera_id"], CAMERA_ID)
        self.assertEqual(listed.await_args.kwargs["source_type"], "motion")

    async def test_alarm_csv_export(self):
        page = {"items": [SAMPLE_EVENT], "total": 1, "limit": 50, "offset": 0}
        with patch(
            "app.services.report_service.list_events",
            new_callable=AsyncMock,
            return_value=page,
        ):
            request = make_mocked_request("GET", "/api/reports/alarms?format=csv")
            request["auth_user"] = ADMIN
            response = await alarm_report_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertIn("text/csv", response.content_type)
        self.assertIn("occurred_at", response.text)
        self.assertIn("recovered", response.text)

    async def test_incident_empty_report(self):
        with patch(
            "app.services.report_service.list_events",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ):
            request = make_mocked_request("GET", "/api/reports/incidents?status=open")
            request["auth_user"] = ADMIN
            response = await incident_report_endpoint(request)
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["report"], "incident")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["total"], 0)

    async def test_incident_includes_message_columns(self):
        row = incident_report_row(SAMPLE_EVENT)
        self.assertEqual(row["message"], "motion detected")
        self.assertEqual(row["title"], "Motion")

    async def test_operator_logs_admin_allowed_operator_denied(self):
        with patch(
            "app.services.report_service.query_audit_logs",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ):
            ok = await operator_logs_report_endpoint(
                _request("GET", "/api/reports/operator-logs", ADMIN)
            )
            denied = await operator_logs_report_endpoint(
                _request("GET", "/api/reports/operator-logs", EVENTS_OP)
            )
        self.assertEqual(ok.status, 200)
        self.assertEqual(denied.status, 403)

    async def test_operator_logs_filter_passed(self):
        with patch(
            "app.services.report_service.query_audit_logs",
            new_callable=AsyncMock,
            return_value={"items": [], "total": 0, "limit": 50, "offset": 0},
        ) as q:
            request = make_mocked_request(
                "GET",
                f"/api/reports/operator-logs?user=a1&action=LOGIN_SUCCESS&camera_id={CAMERA_ID}&limit=25&offset=10",
            )
            request["auth_user"] = SUPER
            response = await operator_logs_report_endpoint(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(q.await_args.kwargs["actor_user_id"], "a1")
        self.assertEqual(q.await_args.kwargs["action"], "LOGIN_SUCCESS")
        self.assertEqual(q.await_args.kwargs["camera_id"], CAMERA_ID)
        self.assertEqual(q.await_args.kwargs["limit"], 25)
        self.assertEqual(q.await_args.kwargs["offset"], 10)
