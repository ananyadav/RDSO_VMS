"""RDSO 18.1.11.8 / 18.1.18 — settings catalog + Admin RBAC."""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.routes.settings_catalog import settings_catalog_endpoint
from app.services.settings_catalog import build_settings_catalog, filter_catalog


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": ["System"]}


def _request(user=None, path="/api/settings/catalog"):
    request = make_mocked_request("GET", path)
    request["auth_user"] = user
    return request


class TestSettingsCatalogService(unittest.TestCase):
    def test_catalog_has_required_scopes_and_fields(self):
        with patch(
            "app.services.settings_catalog.get_storage_settings_public",
            return_value={
                "retention_days": 15,
                "recordings_dir": "C:/Recordings",
                "retention_editable": True,
                "recordings_dir_editable": True,
                "storage_status": "online",
                "storage_status_label": "Online",
            },
        ), patch(
            "app.services.settings_catalog.get_retention_policy",
            return_value={"source": "ui", "label": "15 day(s)"},
        ), patch(
            "app.services.instant_replay_config.instant_replay_public_config",
            return_value={"enabled": True, "maxSeconds": 30},
        ):
            catalog = build_settings_catalog(camera_count=3, master_enabled=True)

        scopes = {i["scope"] for i in catalog["items"]}
        self.assertEqual(scopes, {"vms_server", "recording_server", "camera", "client"})
        for item in catalog["items"]:
            self.assertIn("key", item)
            self.assertIn("name", item)
            self.assertIn("value", item)
            self.assertIn("source", item)
            self.assertIn("editable", item)
            self.assertIn("restart_required", item)

        tz = next(i for i in catalog["items"] if i["key"] == "app.timezone")
        self.assertTrue(tz["restart_required"])
        self.assertFalse(tz["editable"])

        retention = next(i for i in catalog["items"] if i["key"] == "storage.retention_days")
        self.assertTrue(retention["editable"])
        self.assertFalse(retention["restart_required"])

    def test_secrets_not_present_in_catalog(self):
        with patch(
            "app.services.settings_catalog.get_storage_settings_public",
            return_value={
                "retention_days": 7,
                "recordings_dir": "C:/Recordings",
                "retention_editable": True,
                "recordings_dir_editable": True,
            },
        ), patch(
            "app.services.settings_catalog.get_retention_policy",
            return_value={"source": "default"},
        ), patch(
            "app.services.instant_replay_config.instant_replay_public_config",
            return_value={},
        ):
            catalog = build_settings_catalog(camera_count=0)
        blob = json.dumps(catalog).lower()
        self.assertNotIn("mongodb://", blob)
        self.assertNotIn("mongodb+srv://", blob)
        self.assertNotIn("rtsp://admin:", blob)
        self.assertNotIn("[redacted]", blob)  # nothing needed redacting in baseline catalog
        for item in catalog["items"]:
            self.assertFalse(str(item.get("key", "")).endswith("_password"))
            self.assertNotEqual(item.get("value"), "Corp#2024")

    def test_filter_by_scope_and_editable(self):
        catalog = {
            "items": [
                {"key": "a", "scope": "vms_server", "editable": True},
                {"key": "b", "scope": "camera", "editable": False},
            ],
            "scopes": ["vms_server", "camera"],
        }
        only_cam = filter_catalog(catalog, scope="camera")
        self.assertEqual(only_cam["total"], 1)
        self.assertEqual(only_cam["items"][0]["key"], "b")
        editable = filter_catalog(catalog, editable=True)
        self.assertEqual(editable["total"], 1)
        self.assertEqual(editable["items"][0]["key"], "a")


class TestSettingsCatalogRoute(unittest.IsolatedAsyncioTestCase):
    async def test_operator_forbidden(self):
        response = await settings_catalog_endpoint(_request(OPERATOR))
        self.assertEqual(response.status, 403)

    async def test_admin_and_super_allowed(self):
        with patch(
            "app.routes.settings_catalog.camera_collection.count_documents",
            new_callable=AsyncMock,
            return_value=2,
        ), patch(
            "app.routes.settings_catalog.build_settings_catalog",
            return_value={
                "items": [
                    {
                        "key": "storage.retention_days",
                        "name": "Retention",
                        "value": 15,
                        "scope": "vms_server",
                        "source": "ui",
                        "editable": True,
                        "restart_required": False,
                    }
                ],
                "scopes": ["vms_server"],
                "total": 1,
            },
        ):
            admin_resp = await settings_catalog_endpoint(_request(ADMIN))
            super_resp = await settings_catalog_endpoint(_request(SUPER))
        self.assertEqual(admin_resp.status, 200)
        self.assertEqual(super_resp.status, 200)
        body = json.loads(admin_resp.text)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["key"], "storage.retention_days")
