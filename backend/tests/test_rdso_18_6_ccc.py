"""RDSO 18.6 — CCC centralized VMS / video integration."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ccc_vms_source import (
    LocalVmsSource,
    ccc_capability_public,
    ccc_public_camera,
    list_ccc_sources,
    sanitize_ccc_media_payload,
)
from app.services.client_media_routing import build_client_media_routing, live_ws_path


class CapabilityClauses(unittest.TestCase):
    def test_capability_flags_all_target_clauses(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["rdso_18_6"])
        self.assertTrue(cap["browser_only"])
        self.assertFalse(cap["requires_separate_client"])
        self.assertTrue(cap["direct_camera_rtsp_forbidden"])
        self.assertEqual(cap["ui_path"], "/ccc")
        for clause in (
            "18.6.1",
            "18.6.2",
            "18.6.3",
            "18.6.12",
            "18.6.13",
            "18.6.17.1",
            "18.6.17.2",
            "18.6.17.3",
            "18.6.17.4",
            "18.6.18.2",
            "18.6.18.3",
        ):
            self.assertTrue(cap["clauses"][clause], clause)
        self.assertGreaterEqual(cap["external_vms_adapters_registered"], 0)
        self.assertTrue(cap["scale"]["does_not_open_all_streams"])


class NoDirectCamera(unittest.TestCase):
    def test_ccc_public_camera_strips_credentials_and_rtsp(self):
        pub = ccc_public_camera(
            {
                "_id": "abc",
                "name": "Gate",
                "camera_uid": "ip_10_0_0_1",
                "password": "secret",
                "main_rtsp_url": "rtsp://admin:secret@10.0.0.1/main",
                "sub_rtsp_url": "rtsp://admin:secret@10.0.0.1/sub",
                "worker_id": 2,
                "online": True,
            }
        )
        self.assertIsNone(pub["password"])
        self.assertIsNone(pub["main_rtsp_url"])
        self.assertIsNone(pub["sub_rtsp_url"])
        self.assertTrue(pub["identity_stable"])
        self.assertEqual(pub["camera_uid"], "ip_10_0_0_1")

    def test_client_media_sanitizer_drops_rtsp(self):
        raw = build_client_media_routing(
            {"_id": "x", "camera_uid": "ip_1", "worker_id": 1}
        )
        raw["leak"] = "rtsp://admin:pw@1.2.3.4/stream"
        raw["password"] = "nope"
        cleaned = sanitize_ccc_media_payload(raw)
        blob = str(cleaned)
        self.assertNotIn("rtsp://", blob.lower())
        self.assertNotIn("nope", blob)
        self.assertTrue(cleaned["ccc_no_direct_camera"])
        self.assertTrue(cleaned["live"]["ws_path"].startswith("/media/w"))

    def test_live_path_is_vms_go2rtc_not_camera(self):
        path = live_ws_path(3)
        self.assertEqual(path, "/media/w3/api/ws")
        self.assertFalse(path.startswith("rtsp://"))


class LocalSourceAcl(unittest.IsolatedAsyncioTestCase):
    async def test_list_cameras_applies_access_filter_for_non_admin(self):
        source = LocalVmsSource()
        user = {"id": "u1", "role": "Operator", "permissions": ["Live View"]}

        cam_docs = [
            {"_id": "c1", "name": "A", "camera_uid": "ip_1", "is_active": True},
            {"_id": "c2", "name": "B", "camera_uid": "ip_2", "is_active": True},
        ]

        class _Cursor:
            def __init__(self, docs):
                self._docs = docs

            def sort(self, *_a, **_k):
                return self

            def skip(self, *_a, **_k):
                return self

            def limit(self, *_a, **_k):
                return self

            def __aiter__(self):
                async def _gen():
                    for d in self._docs:
                        yield d

                return _gen()

        mock_coll = MagicMock()
        mock_coll.count_documents = AsyncMock(return_value=1)
        mock_coll.find = MagicMock(return_value=_Cursor([cam_docs[0]]))

        with patch("app.core.database.camera_collection", mock_coll):
            with patch(
                "app.services.camera_access.is_admin", return_value=False
            ):
                with patch(
                    "app.services.camera_access.build_access_filter",
                    return_value={"camera_uid": {"$in": ["ip_1"]}},
                ):
                    with patch(
                        "app.services.camera_access.merge_query",
                        side_effect=lambda q, f: {"$and": [q, f]},
                    ):
                        result = await source.list_cameras_public(user, limit=50, offset=0)

        self.assertEqual(result["returned"], 1)
        self.assertTrue(result["streams_not_auto_started"])
        self.assertIsNone(result["items"][0]["password"])

    async def test_large_logical_dataset_paging(self):
        source = LocalVmsSource()
        docs = [
            {"_id": f"id{i}", "name": f"Cam{i}", "camera_uid": f"ip_{i}", "is_active": True}
            for i in range(100)
        ]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *_a, **_k):
                return self

            def skip(self, n):
                self._items = self._items[n:]
                return self

            def limit(self, n):
                self._items = self._items[:n]
                return self

            def __aiter__(self):
                async def _gen():
                    for d in self._items:
                        yield d

                return _gen()

        mock_coll = MagicMock()
        mock_coll.count_documents = AsyncMock(return_value=2500)
        mock_coll.find = MagicMock(return_value=_Cursor(list(docs)))

        with patch("app.core.database.camera_collection", mock_coll):
            with patch("app.services.camera_access.is_admin", return_value=True):
                page = await source.list_cameras_public({"role": "Admin"}, limit=100, offset=0)

        self.assertEqual(page["total"], 2500)
        self.assertEqual(page["limit"], 100)
        self.assertLessEqual(page["returned"], 100)


class SourcesRegistry(unittest.TestCase):
    def test_only_local_source_no_fake_external(self):
        sources = list_ccc_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_id"], "local")
        self.assertFalse(sources[0]["direct_camera_rtsp"])
        self.assertFalse(sources[0]["external_vendor_integration"])
        self.assertTrue(sources[0]["browser_only"])


class RoutesSmoke(unittest.TestCase):
    def test_setup_registers_ccc_paths(self):
        from aiohttp import web
        from app.routes.ccc import setup_ccc_routes

        app = web.Application()
        setup_ccc_routes(app)
        paths = {r.resource.canonical for r in app.router.routes() if hasattr(r, "resource")}
        self.assertIn("/api/ccc/capability", paths)
        self.assertIn("/api/ccc/cameras", paths)
        self.assertIn("/api/ccc/status", paths)


if __name__ == "__main__":
    unittest.main()
