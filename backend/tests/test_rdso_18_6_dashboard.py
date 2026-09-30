"""RDSO 18.6.5 / 18.6.6 / 18.6.15.4 / 18.6.16.1 — CCC dashboard, prefs, hot screen, groups, comms."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_comms_service import (
    acknowledge_communication,
    create_message_template,
    send_incident_communication,
)
from app.services.ccc_compliance_service import incident_compliance
from app.services.ccc_dashboard_prefs import get_dashboard_prefs, save_dashboard_prefs
from app.services.ccc_dashboard_service import get_ccc_dashboard
from app.services.ccc_groups_service import create_ccc_group, list_ccc_groups
from app.services.ccc_hot_screen_service import list_hot_screen_items
from app.services.ccc_incident_service import CRITICAL_PRIORITY_MIN
from app.services.ccc_vms_source import ccc_capability_public


def _admin():
    return {"id": "admin-1", "name": "Admin", "role": "Admin", "permissions": ["Events"]}


def _op(uid="op-2"):
    return {"id": uid, "name": "Op", "role": "Operator", "permissions": ["Events", "Live View"]}


class CapabilityDashboardClauses(unittest.TestCase):
    def test_dashboard_hot_screen_clauses(self):
        cap = ccc_capability_public()
        for c in ("18.6.5", "18.6.6", "18.6.15.4", "18.6.16.1"):
            self.assertTrue(cap["clauses"][c], c)
        self.assertFalse(cap["dashboard"]["fake_statistics"])
        self.assertTrue(cap["dashboard"]["per_user_layout"])
        self.assertTrue(cap["incidents"]["internal_comms_only"])
        self.assertFalse(cap["incidents"]["dmr_tetra"])


class DashboardPrefsIsolation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._store: dict[str, dict] = {}

        async def find_one(query):
            uid = query.get("user_id")
            return self._store.get(uid)

        async def update_one(query, ops, upsert=False):
            uid = query["user_id"]
            doc = self._store.get(uid) or {"user_id": uid}
            if "$set" in ops:
                doc.update(ops["$set"])
            self._store[uid] = doc

        mock = MagicMock()
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)
        self._p = patch("app.services.ccc_dashboard_prefs.prefs_collection", mock)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_prefs_isolated_per_user(self):
        a = await save_dashboard_prefs(
            _admin(),
            widgets=[{"id": "cameras", "enabled": True, "order": 0}, {"id": "events", "enabled": False, "order": 1}],
        )
        b = await save_dashboard_prefs(
            _op(),
            widgets=[{"id": "storage", "enabled": True, "order": 0}],
        )
        self.assertEqual(a["user_id"], "admin-1")
        self.assertEqual(b["user_id"], "op-2")
        a2 = await get_dashboard_prefs(_admin())
        b2 = await get_dashboard_prefs(_op())
        self.assertTrue(any(w["id"] == "cameras" and w["enabled"] for w in a2["widgets"]))
        self.assertTrue(any(w["id"] == "events" and not w["enabled"] for w in a2["widgets"]))
        # Op enabling storage must not flip admin's events back on or change admin widgets
        admin_events = next(w for w in a2["widgets"] if w["id"] == "events")
        self.assertFalse(admin_events["enabled"])
        self.assertTrue(b2["isolated"])
        self.assertNotEqual(
            [w["id"] for w in a2["widgets"] if w["enabled"]],
            [w["id"] for w in b2["widgets"] if w["enabled"]],
        )


class DashboardMetrics(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_uses_real_counts_not_fake(self):
        cam = MagicMock()
        cam.count_documents = AsyncMock(side_effect=[5, 3, 2, 5])  # total, online, offline, flagged
        events = MagicMock()
        events.count_documents = AsyncMock(side_effect=[4, 1])
        events.find = MagicMock(return_value=_async_cursor([]))

        incidents = MagicMock()
        incidents.count_documents = AsyncMock(return_value=0)
        incidents.find = MagicMock(return_value=_async_cursor([]))

        with patch("app.core.database.camera_collection", cam), patch(
            "app.core.database.events_collection", events
        ), patch(
            "app.services.ccc_dashboard_service.incidents_collection", incidents
        ), patch(
            "app.services.ccc_dashboard_service.get_ccc_status_snapshot",
            new=AsyncMock(return_value={"recording": {"ok": True}}),
        ), patch(
            "app.services.ccc_dashboard_service._storage_summary",
            new=AsyncMock(return_value={"ok": True, "free_percent": 40, "status": "ok"}),
        ), patch(
            "app.services.ccc_compliance_service.summarize_compliance",
            new=AsyncMock(return_value={"pending": 0, "overdue": 0, "complete": 0}),
        ), patch(
            "app.services.ccc_hot_screen_service.list_hot_screen_items",
            new=AsyncMock(return_value={"items": []}),
        ):
            data = await get_ccc_dashboard(user=_admin())

        self.assertFalse(data["fake_statistics"])
        self.assertEqual(data["cameras"]["total"], 5)
        self.assertEqual(data["cameras"]["online"], 3)
        self.assertEqual(data["events"]["open"], 4)
        self.assertEqual(data["events"]["critical"], 1)
        self.assertTrue(data["rdso_18_6_5"])
        self.assertTrue(data["rdso_18_6_16_1"])


def _async_cursor(docs):
    class Cursor:
        def sort(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def skip(self, *_a, **_k):
            return self

        def __aiter__(self):
            async def gen():
                for d in docs:
                    yield d

            return gen()

    return Cursor()


class GroupsNotRbac(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._docs: list[dict] = []

        async def insert_one(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            self._docs.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        def find(query):
            return _async_cursor(
                [
                    d
                    for d in self._docs
                    if d.get("enabled", True)
                    and (not query.get("kind") or d.get("kind") == query["kind"])
                ]
            )

        mock = MagicMock()
        mock.insert_one = AsyncMock(side_effect=insert_one)
        mock.find = MagicMock(side_effect=find)
        self._p = patch("app.services.ccc_groups_service.groups_collection", mock)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_create_ops_group_not_rbac(self):
        g = await create_ccc_group(name="RPF Night", kind="rpf", member_user_ids=["u1"])
        self.assertTrue(g["not_rbac_role"])
        self.assertEqual(g["kind"], "rpf")
        items = await list_ccc_groups(kind="rpf")
        self.assertEqual(len(items), 1)


class CommsInternalOnly(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tpls: list[dict] = []
        self._comms: list[dict] = []

        async def tpl_insert(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            self._tpls.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        async def tpl_find_one(query):
            if "_id" in query:
                for d in self._tpls:
                    if d["_id"] == query["_id"]:
                        return d
            return None

        async def comm_insert(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            self._comms.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        async def comm_find_one(query):
            for d in self._comms:
                if d["_id"] == query.get("_id"):
                    return d
            return None

        async def comm_update(query, ops):
            doc = await comm_find_one(query)
            if doc and "$set" in ops:
                doc.update(ops["$set"])

        tpl = MagicMock()
        tpl.insert_one = AsyncMock(side_effect=tpl_insert)
        tpl.find_one = AsyncMock(side_effect=tpl_find_one)
        tpl.find = MagicMock(return_value=_async_cursor(self._tpls))

        comm = MagicMock()
        comm.insert_one = AsyncMock(side_effect=comm_insert)
        comm.find_one = AsyncMock(side_effect=comm_find_one)
        comm.update_one = AsyncMock(side_effect=comm_update)

        self._p1 = patch("app.services.ccc_comms_service.templates_collection", tpl)
        self._p2 = patch("app.services.ccc_comms_service.comms_collection", comm)
        self._p3 = patch(
            "app.services.ccc_incident_service.incidents_collection.update_one",
            new=AsyncMock(),
        )
        self._p1.start()
        self._p2.start()
        self._p3.start()

    async def asyncTearDown(self):
        for p in (self._p1, self._p2, self._p3):
            p.stop()

    async def test_template_and_receipt_no_external_delivery(self):
        tpl = await create_message_template(name="Dispatch", body="Respond to zone")
        self.assertFalse(tpl["external_delivery"])
        self.assertFalse(tpl["dmr_tetra"])
        self.assertFalse(tpl["sms_email"])
        self.assertEqual(tpl["channel"], "ccc_internal")

        comm = await send_incident_communication(
            _admin(),
            incident_id=str(ObjectId()),
            body="",
            template_id=tpl["id"],
            recipient_group="responder",
        )
        self.assertFalse(comm["external_delivery"])
        self.assertFalse(comm["dmr_tetra"])
        self.assertEqual(comm["channel"], "ccc_internal")
        self.assertEqual(comm["status"], "delivered")
        self.assertIsNotNone(comm["sent_at"])
        self.assertIsNotNone(comm["delivered_at"])
        self.assertIsNone(comm["acknowledged_at"])

        ack = await acknowledge_communication(comm["id"], _op())
        self.assertEqual(ack["status"], "acknowledged")
        self.assertIsNotNone(ack["acknowledged_at"])


class ComplianceState(unittest.IsolatedAsyncioTestCase):
    async def test_pending_complete_overdue(self):
        now = datetime.now(timezone.utc)
        doc = {
            "sop_workflow_id": "wf1",
            "sop_step_index": 1,
            "status": "open",
            "assignee_group": "responder",
            "assignee_user_id": "u9",
            "incident_time": now - timedelta(hours=2),
            "created_at": now - timedelta(hours=2),
        }
        wf = {
            "steps": [
                {"order": 0, "title": "Ack"},
                {"order": 1, "title": "Dispatch"},
                {"order": 2, "title": "Close"},
            ],
            "escalation_rules": [{"delay_seconds": 60}],
        }
        with patch(
            "app.services.ccc_compliance_service.get_sop_workflow",
            new=AsyncMock(return_value=wf),
        ):
            c = await incident_compliance(doc)
        self.assertEqual(c["completed_steps"], 1)
        self.assertEqual(c["pending_steps"], 2)
        self.assertTrue(c["overdue"])
        self.assertEqual(c["state"], "overdue")
        self.assertEqual(c["assigned_group"], "responder")


class HotScreenOrderingAcl(unittest.IsolatedAsyncioTestCase):
    async def test_priority_order_and_camera_acl(self):
        now = datetime.now(timezone.utc)
        docs = [
            {
                "_id": ObjectId(),
                "title": "Low critical older",
                "location": "A",
                "status": "open",
                "priority": CRITICAL_PRIORITY_MIN,
                "severity": "critical",
                "incident_time": now - timedelta(hours=3),
                "created_at": now - timedelta(hours=3),
                "linked_camera_ids": ["cam-secret"],
                "assignee_group": "responder",
                "timeline": [],
                "notes": [],
                "linked_event_ids": [],
            },
            {
                "_id": ObjectId(),
                "title": "Higher priority newer",
                "location": "B",
                "status": "escalated",
                "priority": CRITICAL_PRIORITY_MIN + 1,
                "severity": "critical",
                "incident_time": now - timedelta(minutes=5),
                "created_at": now - timedelta(minutes=5),
                "linked_camera_ids": ["cam-ok"],
                "assignee_group": "rpf",
                "timeline": [],
                "notes": [],
                "linked_event_ids": [],
            },
            {
                "_id": ObjectId(),
                "title": "Same P older",
                "location": "C",
                "status": "open",
                "priority": CRITICAL_PRIORITY_MIN + 1,
                "severity": "warning",
                "incident_time": now - timedelta(hours=1),
                "created_at": now - timedelta(hours=1),
                "linked_camera_ids": ["cam-ok"],
                "assignee_group": "ops",
                "timeline": [],
                "notes": [],
                "linked_event_ids": [],
            },
        ]

        # Sort as Mongo would: priority DESC, incident_time ASC
        sorted_docs = sorted(
            docs,
            key=lambda d: (-int(d["priority"]), d["incident_time"]),
        )

        class Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __aiter__(self):
                async def gen():
                    for d in self._items:
                        yield d

                return gen()

        mock = MagicMock()
        mock.find = MagicMock(return_value=Cursor(sorted_docs))

        def can_access(_user, cam_id, _cam=None):
            return cam_id == "cam-ok"

        with patch(
            "app.services.ccc_hot_screen_service.incidents_collection", mock
        ), patch(
            "app.services.ccc_hot_screen_service.is_admin", return_value=False
        ), patch(
            "app.services.ccc_hot_screen_service.get_camera_by_ref",
            new=AsyncMock(return_value={"id": "x"}),
        ), patch(
            "app.services.ccc_hot_screen_service.user_can_access_camera",
            side_effect=can_access,
        ):
            data = await list_hot_screen_items(user=_op(), limit=10)

        titles = [i["title"] for i in data["items"]]
        # Highest priority first; within same priority, oldest first
        self.assertEqual(titles[0], "Same P older")
        self.assertEqual(titles[1], "Higher priority newer")
        self.assertEqual(titles[2], "Low critical older")

        top = data["items"][0]
        self.assertTrue(top["via_vms_only"])
        self.assertFalse(top["direct_camera_rtsp"])
        self.assertTrue(top["live_href"].startswith("/live?camera="))
        self.assertNotIn("rtsp", (top["live_href"] or "").lower())

        restricted = data["items"][2]
        self.assertFalse(restricted["camera_authorized"])
        self.assertIsNone(restricted["camera_id"])
        self.assertIsNone(restricted["live_href"])


if __name__ == "__main__":
    unittest.main()
