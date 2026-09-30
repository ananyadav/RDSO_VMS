"""RDSO 18.1.17 / 18.1.19 / 18.1.20 — multi-server registry + seamless client routing."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.recording_ha import ha_list_servers_endpoint, ha_register_server_endpoint
from app.services.client_media_routing import (
    build_client_media_routing,
    list_recording_servers_public,
    sessions_findable_across_servers,
)
from app.services.recording_ha_store import InMemoryHaStore, reset_ha_store, use_memory_ha_store
from app.services.recording_server_config import recording_server_count_limit


SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": ["Live View"]}


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


class TestRecordingServerLimit(unittest.TestCase):
    def test_no_artificial_server_count_cap(self):
        self.assertIsNone(recording_server_count_limit())


class TestClientMediaRouting(unittest.TestCase):
    def test_live_and_playback_are_relative_and_camera_based(self):
        cam = {
            "_id": "507f1f77bcf86cd799439011",
            "camera_uid": "ip_192_168_41_90",
            "worker_id": 2,
            "recording_server_id": "primary-a",
            "password": "must-not-appear",
            "main_rtsp_url": "rtsp://admin:secret@10.0.0.1/stream",
        }
        routing = build_client_media_routing(cam)
        self.assertEqual(routing["camera_id"], "507f1f77bcf86cd799439011")
        self.assertEqual(routing["camera_uid"], "ip_192_168_41_90")
        self.assertTrue(routing["identity_stable"])
        self.assertTrue(routing["seamless"])
        self.assertEqual(routing["live"]["ws_path"], "/media/w2/api/ws")
        self.assertTrue(routing["live"]["dynamic_switch"])
        self.assertTrue(routing["playback"]["independent_of_recording_server"])
        self.assertIn("{camera_id}", routing["playback"]["media_path_template"])
        self.assertNotIn("password", json.dumps(routing))
        self.assertNotIn("secret", json.dumps(routing))
        self.assertNotIn("rtsp://", json.dumps(routing))
        # Home server is metadata only — not a client connect URL
        self.assertEqual(routing["recording_home_server_id"], "primary-a")
        self.assertTrue(routing["client_must_not_use_server_host"])

    def test_identity_unchanged_when_home_server_reassigned(self):
        cam_a = {"_id": "c1", "camera_uid": "uid-1", "worker_id": 1, "recording_server_id": "primary-a"}
        cam_b = {**cam_a, "recording_server_id": "standby-1"}
        ra = build_client_media_routing(cam_a)
        rb = build_client_media_routing(cam_b)
        self.assertEqual(ra["camera_id"], rb["camera_id"])
        self.assertEqual(ra["camera_uid"], rb["camera_uid"])
        self.assertEqual(ra["live"]["ws_path"], rb["live"]["ws_path"])
        self.assertNotEqual(ra["recording_home_server_id"], rb["recording_home_server_id"])

    def test_playback_sessions_across_server_ids(self):
        sessions = [
            {"camera_id": "c1", "recording_server_id": "primary-a", "id": "s1"},
            {"camera_id": "c1", "recording_server_id": "standby-1", "id": "s2"},
            {"camera_id": "c2", "recording_server_id": "primary-a", "id": "s3"},
        ]
        found = sessions_findable_across_servers(sessions, camera_id="c1")
        self.assertEqual(len(found), 2)
        self.assertEqual({s["recording_server_id"] for s in found}, {"primary-a", "standby-1"})


class TestHaServersListApi(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = use_memory_ha_store(InMemoryHaStore())

    async def asyncTearDown(self):
        reset_ha_store()

    async def test_operator_forbidden(self):
        response = await ha_list_servers_endpoint(_request("GET", "/api/recordings/ha/servers", OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_multiple_logical_servers_no_cap(self):
        for i in range(12):
            await self.store.upsert_server(
                f"rec-{i}",
                {
                    "role": "primary" if i % 2 == 0 else "standby",
                    "enabled": True,
                    "healthy": i != 3,
                    "last_seen": "2026-09-09T00:00:00+00:00",
                    "hostname": f"host-{i}",
                },
            )
        with patch(
            "app.services.recording_ha_coordinator.refresh_server_health_flags",
            new_callable=AsyncMock,
        ):
            data = await list_recording_servers_public()
        self.assertEqual(data["total"], 12)
        self.assertIsNone(data["server_count_limit"])
        self.assertTrue(data["rdso_18_1_17"])

        with patch(
            "app.services.recording_ha_coordinator.refresh_server_health_flags",
            new_callable=AsyncMock,
        ):
            healthy = await list_recording_servers_public(healthy_only=True)
        self.assertEqual(healthy["total"], 11)

        with patch(
            "app.services.recording_ha_coordinator.refresh_server_health_flags",
            new_callable=AsyncMock,
        ):
            response = await ha_list_servers_endpoint(
                _request("GET", "/api/recordings/ha/servers?healthy=true", SUPER)
            )
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["total"], 11)
        self.assertTrue(all(s.get("healthy") for s in body["items"]))
        # public DTO has no credentials
        blob = json.dumps(body)
        self.assertNotIn("password", blob)
        self.assertNotIn("token", blob)

    async def test_register_then_list(self):
        req = _request("POST", "/api/recordings/ha/servers", SUPER)

        async def _json():
            return {"server_id": "logical-z", "role": "standby", "healthy": True}

        req.json = _json  # type: ignore[method-assign]
        created = await ha_register_server_endpoint(req)
        self.assertEqual(created.status, 200)
        with patch(
            "app.services.recording_ha_coordinator.refresh_server_health_flags",
            new_callable=AsyncMock,
        ):
            listed = await list_recording_servers_public()
        self.assertGreaterEqual(listed["total"], 1)
        self.assertTrue(any(s["server_id"] == "logical-z" for s in listed["items"]))
