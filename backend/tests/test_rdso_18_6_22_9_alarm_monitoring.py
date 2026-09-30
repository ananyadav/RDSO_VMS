"""RDSO 18.6.22.9 — CCC alarm monitoring + zone/camera response."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.ccc_alarm_monitoring_service import (
    STATE_ALARM,
    STATE_MONITORING,
    is_alarm_recovered,
    list_alarm_monitoring,
    recover_alarm_monitoring,
    resolve_affected_zone,
    resolve_response_camera,
    save_zone_camera_map,
)
from app.services.client_media_routing import build_client_media_routing, frame_jpeg_path
from app.services.ccc_vms_source import ccc_capability_public


def _admin():
    return {
        "id": "admin-1",
        "name": "Admin",
        "role": "Admin",
        "permissions": ["Events", "Live View"],
    }


def _op_restricted():
    return {
        "id": "op-acl",
        "name": "Op",
        "role": "Operator",
        "permissions": ["Events", "Live View"],
        "allowed_cameras": [],
    }


def _cam(oid=None, *, ptz=False, group="site_a_floor1", path="Site / Bldg / Floor1", uid="cam_a"):
    return {
        "_id": oid or ObjectId(),
        "name": "Cam",
        "display_name": "Cam Display",
        "camera_uid": uid,
        "ptz": ptz,
        "camera_group": group,
        "location_path": path,
        "site": "Site",
        "building": "Bldg",
        "floor": "Floor1",
        "worker_id": 1,
        "is_active": True,
    }


def _async_cursor(docs):
    class Cursor:
        def __init__(self, items=None):
            self._docs = list(items if items is not None else docs)

        def sort(self, *_a, **_k):
            return self

        def limit(self, n):
            self._docs = self._docs[:n]
            return self

        def skip(self, n):
            self._docs = self._docs[n:]
            return self

        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d

            return gen()

    return Cursor()


class CapabilityClause(unittest.TestCase):
    def test_clause_and_paths(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.22.9"])
        am = cap["alarm_monitoring"]
        self.assertEqual(am["path"], "/api/ccc/alarm-monitoring")
        self.assertTrue(am["direct_camera_rtsp_forbidden"])
        self.assertFalse(am["geo_distance_invented"])
        self.assertTrue(am["on_demand_snapshot_via_go2rtc"])


class ZoneResolution(unittest.TestCase):
    def test_explicit_zone_first(self):
        z = resolve_affected_zone(
            {"metadata": {"zone": "Gate-A", "location": "ignored"}},
            _cam(),
        )
        self.assertEqual(z["zone"], "Gate-A")
        self.assertEqual(z["source"], "explicit")
        self.assertFalse(z["unknown"])
        self.assertFalse(z["geo_distance_used"])

    def test_camera_hierarchy_second(self):
        z = resolve_affected_zone({"metadata": {}}, _cam(path="HQ / Lobby"))
        self.assertEqual(z["zone"], "HQ / Lobby")
        self.assertEqual(z["source"], "camera_location_hierarchy")

    def test_unknown_zone_honest(self):
        z = resolve_affected_zone({"metadata": {}}, None)
        self.assertTrue(z["unknown"])
        self.assertIsNone(z["zone"])
        self.assertFalse(z["geo_distance_used"])


class SnapshotRouting(unittest.TestCase):
    def test_frame_jpeg_is_media_path(self):
        path = frame_jpeg_path(2, "uid_sub")
        self.assertTrue(path.startswith("/media/w2/api/frame.jpeg"))
        self.assertIn("src=uid_sub", path)
        self.assertNotIn("rtsp://", path)

    def test_client_media_includes_snapshot(self):
        routing = build_client_media_routing(_cam(uid="abc"))
        snap = routing["snapshot"]
        self.assertTrue(snap["via_vms_go2rtc"])
        self.assertFalse(snap["direct_camera"])
        self.assertTrue(snap["frame_jpeg_path"].startswith("/media/"))


class RecoveredFlags(unittest.TestCase):
    def test_recovered_variants(self):
        self.assertTrue(is_alarm_recovered({"acknowledged": True}))
        self.assertTrue(
            is_alarm_recovered({"status": "open", "metadata": {"monitoring_recovered": True}})
        )
        self.assertTrue(
            is_alarm_recovered({"status": "open", "metadata": {"display_reset": True}})
        )
        self.assertFalse(is_alarm_recovered({"status": "open", "acknowledged": False, "metadata": {}}))


class CameraSelection(unittest.IsolatedAsyncioTestCase):
    async def test_configured_alarm_camera(self):
        fixed = _cam(ptz=False, uid="fixed1")
        with patch(
            "app.services.ccc_alarm_monitoring_service.get_zone_camera_map",
            new=AsyncMock(return_value={"mappings": []}),
        ), patch(
            "app.services.ccc_alarm_monitoring_service._find_related_cameras",
            new=AsyncMock(return_value=[]),
        ):
            out = await resolve_response_camera(
                event_doc={"camera_id": str(fixed["_id"])},
                alarm_camera=fixed,
                zone_info={"zone_key": "hq / lobby", "unknown": False},
                user=_admin(),
            )
        self.assertEqual(out["camera_id"], str(fixed["_id"]))
        self.assertEqual(out["selection_reason"], "configured_alarm_camera")
        self.assertTrue(out["via_vms_only"])
        self.assertFalse(out["direct_camera_rtsp"])
        self.assertTrue(out["snapshot"]["frame_jpeg_path"].startswith("/media/"))

    async def test_related_ptz_preferred(self):
        fixed = _cam(ptz=False, uid="fixed1")
        ptz = _cam(ptz=True, uid="ptz1")
        with patch(
            "app.services.ccc_alarm_monitoring_service.get_zone_camera_map",
            new=AsyncMock(return_value={"mappings": []}),
        ), patch(
            "app.services.ccc_alarm_monitoring_service._find_related_cameras",
            new=AsyncMock(return_value=[fixed, ptz]),
        ):
            out = await resolve_response_camera(
                event_doc={},
                alarm_camera=fixed,
                zone_info={"zone_key": "site / bldg / floor1", "unknown": False},
                user=_admin(),
            )
        self.assertEqual(out["camera_id"], str(ptz["_id"]))
        self.assertEqual(out["selection_reason"], "related_ptz")
        self.assertTrue(out["ptz"])

    async def test_zone_preferred_mapping(self):
        preferred = _cam(ptz=True, uid="pref")
        fixed = _cam(ptz=False, uid="fixed")
        zmap = {
            "mappings": [
                {
                    "zone_key": "gate-a",
                    "zone": "Gate-A",
                    "preferred_camera_id": str(preferred["_id"]),
                    "prefer_ptz": True,
                }
            ]
        }
        with patch(
            "app.services.ccc_alarm_monitoring_service.get_camera_by_ref",
            new=AsyncMock(return_value=preferred),
        ):
            out = await resolve_response_camera(
                event_doc={},
                alarm_camera=fixed,
                zone_info={"zone_key": "gate-a", "unknown": False},
                zone_map=zmap,
                user=_admin(),
            )
        self.assertEqual(out["camera_id"], str(preferred["_id"]))
        self.assertEqual(out["selection_reason"], "zone_preferred_mapping")


class MonitoringLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cam = _cam()
        self.events = []

    def _event(self, *, priority=3, severity="warning", title="t", recovered=False):
        doc = {
            "_id": ObjectId(),
            "camera_id": str(self.cam["_id"]),
            "camera_uid": self.cam["camera_uid"],
            "source_type": "manual_test",
            "severity": severity,
            "priority": priority,
            "title": title,
            "message": "synthetic",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "status": "open",
            "acknowledged": False,
            "metadata": {"zone": "Test-Zone"} if not recovered else {"monitoring_recovered": True},
        }
        self.events.append(doc)
        return doc

    async def test_monitoring_to_alarm_to_monitoring(self):
        e1 = self._event(priority=5, severity="critical", title="high")
        e2 = self._event(priority=2, severity="info", title="low")

        events_col = MagicMock()
        events_col.find = MagicMock(return_value=_async_cursor(list(self.events)))

        async def find_one(q):
            if "_id" in q:
                for d in self.events:
                    if d["_id"] == q["_id"]:
                        return d
            return None

        async def update_one(q, ops):
            doc = await find_one(q)
            if doc and "$set" in ops:
                for k, v in ops["$set"].items():
                    if k.startswith("metadata."):
                        md = doc.setdefault("metadata", {})
                        md[k.split(".", 1)[1]] = v
                    else:
                        doc[k] = v

        events_col.find_one = AsyncMock(side_effect=find_one)
        events_col.update_one = AsyncMock(side_effect=update_one)

        with patch(
            "app.services.ccc_alarm_monitoring_service.events_collection", events_col
        ), patch(
            "app.services.ccc_alarm_monitoring_service.get_zone_camera_map",
            new=AsyncMock(return_value={"mappings": []}),
        ), patch(
            "app.services.ccc_alarm_monitoring_service.get_camera_by_ref",
            new=AsyncMock(return_value=self.cam),
        ), patch(
            "app.services.ccc_alarm_monitoring_service._find_related_cameras",
            new=AsyncMock(return_value=[]),
        ):
            mon = await list_alarm_monitoring(user=_admin(), limit=10)
            self.assertEqual(mon["monitoring_state"], STATE_ALARM)
            self.assertTrue(mon["is_alarm"])
            # Higher priority first — must not be displaced by lower
            self.assertEqual(mon["items"][0]["event_id"], str(e1["_id"]))
            self.assertEqual(mon["items"][0]["priority"], 5)
            self.assertEqual(mon["items"][1]["priority"], 2)
            self.assertTrue(mon["direct_camera_rtsp_forbidden"])
            self.assertTrue(mon["items"][0]["via_vms_only"])
            self.assertTrue(
                mon["items"][0]["snapshot"]["frame_jpeg_path"].startswith("/media/")
            )

            recovered = await recover_alarm_monitoring(str(e1["_id"]), _admin())
            self.assertTrue(recovered["metadata"].get("monitoring_recovered"))

            # After recover high: still ALARM with low remaining
            events_col.find = MagicMock(
                return_value=_async_cursor([d for d in self.events if not is_alarm_recovered(d)])
            )
            mon2 = await list_alarm_monitoring(user=_admin(), limit=10)
            self.assertEqual(mon2["monitoring_state"], STATE_ALARM)
            self.assertEqual(mon2["items"][0]["event_id"], str(e2["_id"]))

            await recover_alarm_monitoring(str(e2["_id"]), _admin())
            events_col.find = MagicMock(return_value=_async_cursor([]))
            mon3 = await list_alarm_monitoring(user=_admin(), limit=10)
            self.assertEqual(mon3["monitoring_state"], STATE_MONITORING)
            self.assertFalse(mon3["is_alarm"])
            self.assertEqual(mon3["active_count"], 0)

    async def test_acl_denies_unauthorized_camera_response(self):
        self._event()
        events_col = MagicMock()
        events_col.find = MagicMock(return_value=_async_cursor(list(self.events)))
        with patch(
            "app.services.ccc_alarm_monitoring_service.events_collection", events_col
        ), patch(
            "app.services.ccc_alarm_monitoring_service.get_zone_camera_map",
            new=AsyncMock(return_value={"mappings": []}),
        ), patch(
            "app.services.ccc_alarm_monitoring_service.get_camera_by_ref",
            new=AsyncMock(return_value=self.cam),
        ), patch(
            "app.services.ccc_alarm_monitoring_service.user_can_access_camera",
            return_value=False,
        ), patch(
            "app.services.ccc_alarm_monitoring_service.is_admin",
            return_value=False,
        ):
            mon = await list_alarm_monitoring(user=_op_restricted(), limit=10)
        # Event filtered out entirely when camera ACL fails
        self.assertEqual(mon["active_count"], 0)
        self.assertEqual(mon["monitoring_state"], STATE_MONITORING)


class ZoneMapPersist(unittest.IsolatedAsyncioTestCase):
    async def test_save_and_validate(self):
        store = {}

        async def update_one(q, ops, upsert=False):
            store["doc"] = {"_id": q["_id"], **ops.get("$set", {})}

        async def find_one(q):
            return store.get("doc")

        mock = MagicMock()
        mock.update_one = AsyncMock(side_effect=update_one)
        mock.find_one = AsyncMock(side_effect=find_one)
        cid = str(ObjectId())
        with patch("app.services.ccc_alarm_monitoring_service._zone_map", mock):
            out = await save_zone_camera_map(
                [{"zone": "Lobby", "preferred_camera_id": cid, "prefer_ptz": True}]
            )
        self.assertEqual(len(out["mappings"]), 1)
        self.assertEqual(out["mappings"][0]["preferred_camera_id"], cid)


if __name__ == "__main__":
    unittest.main()
