"""RDSO 18.6.22.18 — CCC historical reporting / export."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.app_timezone import clear_app_timezone_cache, local_day_bounds_utc
from app.services.ccc_historical_report_service import (
    CCC_REPORT_CSV_MAX,
    CCC_REPORT_PAGE_MAX,
    build_ccc_activity_history,
    build_ccc_communications_history,
    build_ccc_compliance_history,
    build_ccc_event_history,
    build_ccc_incident_history,
    ccc_incident_report_row,
    export_csv,
    export_json,
    redact_export_row,
    resolve_report_time_bounds,
)
from app.services.ccc_vms_source import ccc_capability_public
from app.services.report_service import rows_to_csv


def _admin():
    return {"id": "a1", "name": "Admin", "role": "Admin", "permissions": ["Events"]}


def _op():
    return {
        "id": "o1",
        "name": "Op",
        "role": "Operator",
        "permissions": ["Events", "Live View"],
    }


class Capability(unittest.TestCase):
    def test_clause_flagged(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.22.18"])
        self.assertFalse(cap["historical_reports"]["duplicate_collections"])
        self.assertIn("csv", cap["historical_reports"]["formats"])
        self.assertFalse(cap["historical_reports"]["pdf"])


class TimezoneBounds(unittest.TestCase):
    def setUp(self):
        os.environ["APP_TIMEZONE"] = "Asia/Kolkata"
        clear_app_timezone_cache()

    def tearDown(self):
        clear_app_timezone_cache()

    def test_date_only_uses_app_timezone(self):
        start, end, name = resolve_report_time_bounds("2026-09-10", "2026-09-10")
        self.assertEqual(name, "Asia/Kolkata")
        expected_start, expected_end = local_day_bounds_utc("2026-09-10")
        self.assertEqual(start, expected_start)
        self.assertEqual(end, expected_end)
        # Kolkata is UTC+5:30 → day starts previous UTC evening
        self.assertEqual(start.hour, 18)
        self.assertEqual(start.minute, 30)


class Redaction(unittest.TestCase):
    def test_secret_keys_and_rtsp_stripped(self):
        row = redact_export_row(
            {
                "title": "ok",
                "password": "secret",
                "api_token": "tok",
                "url": "rtsp://user:pass@cam/stream",
                "metadata": {"token": "x", "camera_id": "c1"},
            }
        )
        self.assertEqual(row["title"], "ok")
        self.assertNotIn("password", row)
        self.assertNotIn("api_token", row)
        self.assertEqual(row["url"], "***")
        self.assertEqual((row.get("metadata") or {}).get("token"), "[REDACTED]")
        self.assertEqual((row.get("metadata") or {}).get("camera_id"), "c1")


class IncidentHistory(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        now = datetime.now(timezone.utc)
        self.docs = [
            {
                "_id": ObjectId(),
                "title": "Breach",
                "status": "open",
                "priority": 5,
                "severity": "critical",
                "assignee_group": "responder",
                "assignee_user_id": "u1",
                "assignee_user_name": "R1",
                "location": "Platform 1",
                "linked_camera_ids": ["cam-ok"],
                "linked_event_ids": ["ev1"],
                "incident_time": now - timedelta(hours=1),
                "created_at": now - timedelta(hours=1),
                "updated_at": now,
                "sop_workflow_id": None,
                "sop_step_index": 0,
                "timeline": [],
                "notes": [],
            },
            {
                "_id": ObjectId(),
                "title": "Old closed",
                "status": "closed",
                "priority": 2,
                "severity": "info",
                "assignee_group": "ops",
                "location": "Yard",
                "linked_camera_ids": ["cam-secret"],
                "linked_event_ids": [],
                "incident_time": now - timedelta(days=40),
                "created_at": now - timedelta(days=40),
                "updated_at": now - timedelta(days=40),
                "sop_step_index": 0,
                "timeline": [],
                "notes": [],
            },
        ]

        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_a, **_k):
                return self

            def skip(self, n):
                self._docs = self._docs[n:]
                return self

            def limit(self, n):
                self._docs = self._docs[:n]
                return self

            def __aiter__(self):
                async def gen():
                    for d in self._docs:
                        yield d

                return gen()

        mock = MagicMock()
        mock.count_documents = AsyncMock(return_value=len(self.docs))
        mock.find = MagicMock(side_effect=lambda q: Cursor(list(self.docs)))
        self._p = patch(
            "app.services.ccc_historical_report_service.incidents_collection", mock
        )
        self._p.start()
        self._perm = patch(
            "app.services.ccc_historical_report_service.user_can_read_incidents",
            return_value=True,
        )
        self._perm.start()

        async def acl(user, cams):
            from app.services.ccc_incident_service import IncidentPermissionError

            for c in cams:
                if c == "cam-secret":
                    raise IncidentPermissionError("denied")

        self._acl = patch(
            "app.services.ccc_historical_report_service._assert_camera_acl",
            new=AsyncMock(side_effect=acl),
        )
        self._acl.start()

    async def asyncTearDown(self):
        for p in (self._p, self._perm, self._acl):
            p.stop()

    async def test_filters_and_acl(self):
        data = await build_ccc_incident_history(_op(), limit=50)
        self.assertTrue(data["rdso_18_6_22_18"])
        self.assertTrue(data["camera_acl_applied"])
        # ACL drops cam-secret row from items
        titles = [r["title"] for r in data["items"]]
        self.assertIn("Breach", titles)
        self.assertNotIn("Old closed", titles)
        self.assertIn("Platform 1", data["items"][0]["location"])
        self.assertIn("cam-ok", data["items"][0]["linked_camera_ids"])

    async def test_status_filter_query_built(self):
        with patch(
            "app.services.ccc_historical_report_service.incidents_collection"
        ) as mock:
            mock.count_documents = AsyncMock(return_value=0)

            class Empty:
                def sort(self, *_a, **_k):
                    return self

                def skip(self, *_a, **_k):
                    return self

                def limit(self, *_a, **_k):
                    return self

                def __aiter__(self):
                    async def gen():
                        if False:
                            yield None

                    return gen()

            mock.find = MagicMock(return_value=Empty())
            data = await build_ccc_incident_history(
                _admin(), status="escalated", assignee_group="rpf", priority=5
            )
            self.assertTrue(data["empty"])
            q = mock.find.call_args[0][0]
            self.assertEqual(q["status"], "escalated")
            self.assertEqual(q["assignee_group"], "rpf")
            self.assertEqual(q["priority"], 5)


class EventHistory(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_list_events_acl(self):
        sample = {
            "id": "e1",
            "camera_id": "c1",
            "camera_uid": "uid1",
            "source_type": "motion",
            "severity": "warning",
            "priority": 3,
            "title": "Motion",
            "occurred_at": "2026-09-10T10:00:00+00:00",
            "status": "open",
            "acknowledged": False,
            "metadata": {},
        }
        with patch(
            "app.services.ccc_historical_report_service.list_events",
            new=AsyncMock(
                return_value={"items": [sample], "total": 1, "limit": 50, "offset": 0}
            ),
        ) as le:
            data = await build_ccc_event_history(
                _op(), camera_id="c1", source_type="motion", severity="warning"
            )
            self.assertEqual(data["report"], "ccc_event_history")
            self.assertEqual(data["items"][0]["source_type"], "motion")
            self.assertEqual(data["items"][0]["alarm_state"], "open")
            self.assertTrue(data["camera_acl_applied"])
            le.assert_awaited()
            kwargs = le.await_args.kwargs
            self.assertEqual(kwargs["camera_id"], "c1")


class ActivityHistory(unittest.IsolatedAsyncioTestCase):
    async def test_operator_filters(self):
        item = {
            "id": "a1",
            "timestamp": "2026-09-10T12:00:00+00:00",
            "actor_user_id": "u1",
            "actor_username": "ops",
            "actor_role": "Admin",
            "action": "CCC_INCIDENT_CREATED",
            "resource_type": "ccc_incident",
            "resource_id": "i1",
            "resource_label": "",
            "success": True,
            "status": "success",
            "metadata": {"password": "nope", "camera_id": "c1"},
        }
        with patch(
            "app.services.ccc_historical_report_service.query_audit_logs",
            new=AsyncMock(return_value={"items": [item], "total": 1, "limit": 50, "offset": 0}),
        ):
            data = await build_ccc_activity_history(
                actor_user_id="u1", action="CCC_INCIDENT_CREATED"
            )
        self.assertEqual(data["items"][0]["action"], "CCC_INCIDENT_CREATED")
        self.assertEqual(data["items"][0]["camera_id"], "c1")
        self.assertNotIn("password", data["items"][0])


class CommsCompliance(unittest.IsolatedAsyncioTestCase):
    async def test_communications_receipts(self):
        docs = [
            {
                "_id": ObjectId(),
                "incident_id": "inc1",
                "subject": "Dispatch",
                "body": "Go",
                "status": "acknowledged",
                "recipient_group": "responder",
                "sent_at": datetime.now(timezone.utc),
                "delivered_at": datetime.now(timezone.utc),
                "acknowledged_at": datetime.now(timezone.utc),
                "sender": {},
                "external_delivery": False,
            }
        ]

        class Cursor:
            def sort(self, *_a, **_k):
                return self

            def skip(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    for d in docs:
                        yield d

                return gen()

        mock = MagicMock()
        mock.count_documents = AsyncMock(return_value=1)
        mock.find = MagicMock(return_value=Cursor())
        with patch("app.services.ccc_comms_service.comms_collection", mock):
            data = await build_ccc_communications_history(status="acknowledged")
        self.assertEqual(data["items"][0]["status"], "acknowledged")
        self.assertFalse(data["items"][0]["external_delivery"])
        self.assertFalse(data["dmr_tetra"])

    async def test_compliance_derived(self):
        now = datetime.now(timezone.utc)
        doc = {
            "_id": ObjectId(),
            "title": "SOP case",
            "status": "open",
            "priority": 4,
            "severity": "critical",
            "sop_workflow_id": "wf1",
            "sop_step_index": 0,
            "assignee_group": "responder",
            "linked_camera_ids": [],
            "linked_event_ids": [],
            "incident_time": now,
            "created_at": now,
            "timeline": [],
            "notes": [],
        }

        class Cursor:
            def sort(self, *_a, **_k):
                return self

            def skip(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    yield doc

                return gen()

        mock = MagicMock()
        mock.count_documents = AsyncMock(return_value=1)
        mock.find = MagicMock(return_value=Cursor())
        with patch(
            "app.services.ccc_historical_report_service.incidents_collection", mock
        ), patch(
            "app.services.ccc_historical_report_service.user_can_read_incidents",
            return_value=True,
        ), patch(
            "app.services.ccc_historical_report_service._assert_camera_acl",
            new=AsyncMock(),
        ), patch(
            "app.services.ccc_historical_report_service.incident_compliance",
            new=AsyncMock(
                return_value={
                    "state": "pending",
                    "completed_steps": 0,
                    "pending_steps": 2,
                    "total_steps": 2,
                    "overdue": False,
                    "due_at": None,
                    "assigned_group": "responder",
                    "assigned_user_id": None,
                }
            ),
        ):
            data = await build_ccc_compliance_history(_admin(), state="pending")
        self.assertEqual(data["items"][0]["compliance_state"], "pending")
        self.assertTrue(data["derived"])


class ExportFormats(unittest.TestCase):
    def test_csv_and_json_export(self):
        pub = {
            "id": "i1",
            "title": "T",
            "status": "open",
            "priority": 4,
            "severity": "critical",
            "incident_time": "2026-09-10T10:00:00+00:00",
            "assignee_group": "responder",
            "assignee_user_id": "",
            "assignee_user_name": "",
            "location": "A",
            "linked_camera_ids": ["c1"],
            "linked_event_ids": ["e1"],
            "sop_workflow_id": "",
            "sop_step_index": 0,
        }
        row = ccc_incident_report_row(pub)
        name, csv_body = export_csv("ccc_incident_history", [row])
        self.assertTrue(name.endswith(".csv"))
        self.assertIn("incident_id", csv_body)
        self.assertIn("T", csv_body)
        # reuse rows_to_csv stability
        self.assertIn(",", csv_body)

        payload = {
            "report": "ccc_incident_history",
            "items": [row, {"password": "x", "title": "leak"}],
            "total": 2,
        }
        jname, jbody = export_json("ccc_incident_history", payload)
        self.assertTrue(jname.endswith(".json"))
        parsed = json.loads(jbody)
        self.assertEqual(parsed["report"], "ccc_incident_history")
        self.assertNotIn("password", parsed["items"][1])


class PaginationCaps(unittest.TestCase):
    def test_page_vs_csv_caps(self):
        self.assertEqual(CCC_REPORT_PAGE_MAX, 200)
        self.assertEqual(CCC_REPORT_CSV_MAX, 2000)
        self.assertLess(CCC_REPORT_PAGE_MAX, CCC_REPORT_CSV_MAX)


class LargeLogicalDataset(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_materialize_all_docs(self):
        """Cursor limit must be applied — simulate large total with small page."""
        calls = {"limit": None}

        class Cursor:
            def sort(self, *_a, **_k):
                return self

            def skip(self, *_a, **_k):
                return self

            def limit(self, n):
                calls["limit"] = n
                return self

            def __aiter__(self):
                async def gen():
                    return
                    yield  # pragma: no cover

                return gen()

        mock = MagicMock()
        mock.count_documents = AsyncMock(return_value=50_000)
        mock.find = MagicMock(return_value=Cursor())
        with patch(
            "app.services.ccc_historical_report_service.incidents_collection", mock
        ), patch(
            "app.services.ccc_historical_report_service.user_can_read_incidents",
            return_value=True,
        ):
            data = await build_ccc_incident_history(_admin(), limit=25, offset=100)
        self.assertEqual(data["total"], 50_000)
        self.assertEqual(calls["limit"], 25)
        self.assertEqual(data["offset"], 100)
        self.assertEqual(data["returned"], 0)


class RouteRbac(unittest.IsolatedAsyncioTestCase):
    async def test_activity_requires_admin(self):
        from aiohttp.test_utils import make_mocked_request

        from app.routes.ccc_reports import ccc_activity_history_endpoint

        req = make_mocked_request("GET", "/api/ccc/reports/activity")
        req["auth_user"] = _op()
        resp = await ccc_activity_history_endpoint(req)
        self.assertEqual(resp.status, 403)

    async def test_events_requires_events_permission(self):
        from aiohttp.test_utils import make_mocked_request

        from app.routes.ccc_reports import ccc_event_history_endpoint

        req = make_mocked_request("GET", "/api/ccc/reports/events")
        req["auth_user"] = {
            "id": "x",
            "role": "Operator",
            "permissions": ["Live View"],
        }
        resp = await ccc_event_history_endpoint(req)
        self.assertEqual(resp.status, 403)


if __name__ == "__main__":
    unittest.main()
