"""RDSO 18.6.4 / 18.6.7 / 18.6.15.2 / 18.6.20 / 18.6.22.13 / 18.6.22.14 — CCC incidents."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_incident_escalation import process_due_escalations, rule_matches_incident
from app.services.ccc_incident_service import (
    CRITICAL_PRIORITY_MIN,
    IncidentDuplicateError,
    IncidentValidationError,
    create_incident,
    incident_to_public,
    promote_event_to_incident,
    update_incident,
)
from app.services.ccc_sop_service import (
    create_sop_workflow,
    list_sop_workflows,
    update_sop_workflow,
)
from app.services.ccc_vms_source import ccc_capability_public


def _admin():
    return {"id": "a1", "name": "Admin", "role": "Admin", "permissions": ["Events"]}


class CapabilityIncidentClauses(unittest.TestCase):
    def test_incident_clauses_flagged(self):
        cap = ccc_capability_public()
        for c in ("18.6.4", "18.6.7", "18.6.15.2", "18.6.20", "18.6.22.13", "18.6.22.14"):
            self.assertTrue(cap["clauses"][c], c)
        self.assertTrue(cap["incidents"]["not_a_second_alarm_engine"])
        self.assertFalse(cap["incidents"]["dmr_tetra"])


class IncidentModel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._docs: list[dict] = []

        async def insert_one(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            self._docs.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        async def find_one(query):
            if "_id" in query:
                for d in self._docs:
                    if d["_id"] == query["_id"]:
                        return d
            if "linked_event_ids" in query:
                eid = query["linked_event_ids"]
                for d in self._docs:
                    if eid in (d.get("linked_event_ids") or []):
                        return d
            return None

        async def update_one(query, ops):
            doc = await find_one(query)
            if not doc:
                return
            if "$set" in ops:
                doc.update(ops["$set"])
            if "$push" in ops:
                for k, v in ops["$push"].items():
                    if isinstance(v, dict) and "$each" in v:
                        doc.setdefault(k, []).extend(v["$each"])
                    else:
                        doc.setdefault(k, []).append(v)

        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_a, **_k):
                return self

            def skip(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    for d in self._docs:
                        yield d

                return gen()

        mock = MagicMock()
        mock.insert_one = AsyncMock(side_effect=insert_one)
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)
        mock.count_documents = AsyncMock(return_value=0)
        mock.find = MagicMock(side_effect=lambda q: Cursor(self._docs))
        self._patcher = patch(
            "app.services.ccc_incident_service.incidents_collection", mock
        )
        self._patcher.start()
        self._acl = patch(
            "app.services.ccc_incident_service._assert_camera_acl", new=AsyncMock()
        )
        self._acl.start()
        self._perm = patch(
            "app.services.ccc_incident_service.user_can_write_incidents", return_value=True
        )
        self._perm_r = patch(
            "app.services.ccc_incident_service.user_can_read_incidents", return_value=True
        )
        self._perm.start()
        self._perm_r.start()

    async def asyncTearDown(self):
        for p in (self._patcher, self._acl, self._perm, self._perm_r):
            p.stop()

    async def test_manual_incident_requires_title(self):
        with self.assertRaises(IncidentValidationError):
            await create_incident(_admin(), title="")

    async def test_manual_incident_fields(self):
        inc = await create_incident(
            _admin(),
            title="Fence breach",
            location="Zone A",
            severity="critical",
            priority=5,
            assignee_group="responder",
            linked_camera_ids=["cam1"],
        )
        self.assertEqual(inc["title"], "Fence breach")
        self.assertEqual(inc["location"], "Zone A")
        self.assertEqual(inc["priority"], 5)
        self.assertTrue(inc["critical"])
        self.assertEqual(inc["assignee_group"], "responder")
        self.assertTrue(any(t["type"] == "created" for t in inc["timeline"]))

    async def test_promote_event_and_duplicate_prevention(self):
        event = {
            "id": "ev1",
            "title": "Signal loss",
            "severity": "critical",
            "priority": 5,
            "occurred_at": "2026-09-10T10:00:00+00:00",
            "camera_id": "cam1",
            "camera_uid": "ip_1",
            "metadata": {"location": "Gate"},
        }
        with patch(
            "app.services.event_service.get_event", new=AsyncMock(return_value=event)
        ):
            first = await promote_event_to_incident(_admin(), "ev1")
            self.assertIn("ev1", first["linked_event_ids"])
            with self.assertRaises(IncidentDuplicateError):
                await promote_event_to_incident(_admin(), "ev1")

    async def test_multi_author_update_timeline(self):
        inc = await create_incident(_admin(), title="T1", linked_camera_ids=[])
        other = {"id": "u2", "name": "Op2", "role": "Operator", "permissions": ["Events"]}
        updated = await update_incident(
            inc["id"], other, patch={"status": "in_progress", "comment": "On site"}
        )
        types = [t["type"] for t in updated["timeline"]]
        self.assertIn("status_change", types)
        self.assertIn("comment", types)
        self.assertEqual(updated["updated_by"]["name"], "Op2")


class EscalationRules(unittest.TestCase):
    def test_priority_and_location_match(self):
        rule = {
            "enabled": True,
            "min_priority": 4,
            "location_prefix": "zone-a",
            "from_statuses": ["open", "assigned"],
        }
        self.assertTrue(
            rule_matches_incident(
                rule, {"status": "open", "priority": 5, "location": "Zone-A North"}
            )
        )
        self.assertFalse(
            rule_matches_incident(
                rule, {"status": "open", "priority": 2, "location": "Zone-A"}
            )
        )
        self.assertFalse(
            rule_matches_incident(
                rule, {"status": "open", "priority": 5, "location": "Zone-B"}
            )
        )


class SopHotReload(unittest.IsolatedAsyncioTestCase):
    async def test_sop_update_without_restart_flag(self):
        store: list[dict] = []

        async def insert_one(doc):
            doc = dict(doc)
            doc["_id"] = ObjectId()
            store.append(doc)

            class R:
                inserted_id = doc["_id"]

            return R()

        class Cursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    for d in self._docs:
                        yield d

                return gen()

        async def find_one(query):
            for d in store:
                if d["_id"] == query.get("_id"):
                    return d
            return None

        async def update_one(query, ops):
            d = await find_one(query)
            if d and "$set" in ops:
                d.update(ops["$set"])

        mock = MagicMock()
        mock.insert_one = AsyncMock(side_effect=insert_one)
        mock.find = MagicMock(side_effect=lambda query: Cursor(list(store)))
        mock.find_one = AsyncMock(side_effect=find_one)
        mock.update_one = AsyncMock(side_effect=update_one)

        with patch("app.services.ccc_sop_service.sop_collection", mock):
            wf = await create_sop_workflow(
                name="SOP1",
                steps=["A", "B"],
                escalation_rules=[
                    {
                        "id": "r1",
                        "delay_seconds": 60,
                        "min_priority": 4,
                        "assign_group": "RPF",
                    }
                ],
            )
            self.assertTrue(wf["hot_reload"])
            self.assertFalse(wf["requires_restart"])
            updated = await update_sop_workflow(
                wf["id"],
                {
                    "steps": ["A", "B", "C"],
                    "escalation_rules": [
                        {
                            "id": "r1",
                            "delay_seconds": 30,
                            "min_priority": 5,
                            "assign_group": "RPF",
                        }
                    ],
                },
            )
            self.assertEqual(len(updated["steps"]), 3)
            self.assertEqual(updated["escalation_rules"][0]["delay_seconds"], 30)
            listed = await list_sop_workflows(enabled_only=True)
            self.assertEqual(listed[0]["escalation_rules"][0]["delay_seconds"], 30)


class CriticalOrdering(unittest.TestCase):
    def test_critical_threshold(self):
        self.assertGreaterEqual(CRITICAL_PRIORITY_MIN, 4)
        pub = incident_to_public(
            {
                "_id": ObjectId(),
                "title": "c",
                "priority": 5,
                "severity": "critical",
                "status": "open",
                "linked_event_ids": [],
                "linked_camera_ids": [],
                "notes": [],
                "timeline": [],
            }
        )
        self.assertTrue(pub["critical"])


class EscalationProcess(unittest.IsolatedAsyncioTestCase):
    async def test_process_due_escalations_assigns_rpf(self):
        now = datetime.now(timezone.utc)
        doc = {
            "_id": ObjectId(),
            "status": "open",
            "priority": 5,
            "location": "Yard",
            "incident_time": now - timedelta(seconds=500),
            "created_at": now - timedelta(seconds=500),
            "escalation": {"count": 0},
            "linked_event_ids": [],
            "linked_camera_ids": [],
            "notes": [],
            "timeline": [],
            "title": "Critical",
            "severity": "critical",
        }

        class Cursor:
            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def gen():
                    yield doc

                return gen()

        with patch(
            "app.services.ccc_incident_escalation.get_default_escalation_rules",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "rpf1",
                        "delay_seconds": 60,
                        "min_priority": 4,
                        "assign_group": "RPF",
                        "from_statuses": ["open"],
                        "enabled": True,
                        "location_prefix": "",
                    }
                ]
            ),
        ):
            with patch(
                "app.services.ccc_incident_escalation.incidents_collection"
            ) as coll:
                coll.find = MagicMock(return_value=Cursor())
                with patch(
                    "app.services.ccc_incident_escalation.assign_incident_responder",
                    new=AsyncMock(
                        return_value={
                            "id": str(doc["_id"]),
                            "assignee_group": "RPF",
                            "status": "escalated",
                        }
                    ),
                ) as assign:
                    result = await process_due_escalations(limit=10)
                    self.assertEqual(result["escalated"], 1)
                    self.assertFalse(result["dmr_tetra"])
                    assign.assert_awaited()


class RoutesRegistered(unittest.TestCase):
    def test_incident_routes_registered(self):
        from aiohttp import web
        from app.routes.ccc import setup_ccc_routes

        app = web.Application()
        setup_ccc_routes(app)
        paths = {getattr(r.resource, "canonical", "") for r in app.router.routes()}
        self.assertIn("/api/ccc/event-log", paths)
        self.assertIn("/api/ccc/incidents", paths)
        self.assertIn("/api/ccc/sop-workflows", paths)


if __name__ == "__main__":
    unittest.main()
