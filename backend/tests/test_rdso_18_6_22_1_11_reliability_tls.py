"""RDSO 18.6.22.1 / 18.6.22.11 — startup timing, HTTPS enforce, TLS capability."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from app.core.startup_state import (
    STARTUP_BUDGET_SECONDS,
    STARTUP_KEY,
    health_handler,
    mark_listen,
    mark_ready,
    mark_startup_begin,
    new_startup_state,
    ready_handler,
    startup_middleware,
)
from app.services.ccc_transport_security import (
    allow_insecure_request,
    https_enforce_middleware,
    tls_capability_public,
)
from app.services.ccc_vms_source import ccc_capability_public
from app.services.session_service import session_cookie_kwargs


class Capability(unittest.TestCase):
    def test_clauses(self):
        cap = ccc_capability_public()
        self.assertTrue(cap["clauses"]["18.6.22.1"])
        self.assertTrue(cap["clauses"]["18.6.22.11"])
        self.assertEqual(cap["reliability"]["startup_budget_seconds"], 300)
        self.assertEqual(cap["transport_security"]["min_symmetric_bits"], 128)


class StartupTiming(unittest.IsolatedAsyncioTestCase):
    async def test_health_timing_and_ready_gate(self):
        app = web.Application(middlewares=[startup_middleware])
        state = new_startup_state()
        mark_startup_begin(state)
        mark_listen(state)
        app[STARTUP_KEY] = state
        app.router.add_get("/api/health", health_handler)
        app.router.add_get("/api/ready", ready_handler)

        async def ok(_r):
            return web.json_response({"ok": True})

        app.router.add_get("/api/ccc/devices", ok)

        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            h = await client.get("/api/health")
            self.assertEqual(h.status, 200)
            data = await h.json()
            self.assertFalse(data["ready"])
            self.assertIsNotNone(data["startup_started_at"])
            self.assertIsNotNone(data["listen_at"])
            self.assertIsNone(data["ready_at"])
            self.assertEqual(data["startup_budget_seconds"], STARTUP_BUDGET_SECONDS)

            r = await client.get("/api/ready")
            self.assertEqual(r.status, 503)

            blocked = await client.get("/api/ccc/devices")
            self.assertEqual(blocked.status, 503)
            body = await blocked.json()
            self.assertFalse(body["ready"])

            mark_ready(state)
            self.assertTrue(state["ready"])
            self.assertIsNotNone(state["startup_duration_seconds"])
            self.assertLessEqual(state["startup_duration_seconds"], STARTUP_BUDGET_SECONDS)

            r2 = await client.get("/api/ready")
            self.assertEqual(r2.status, 200)
            ready_body = await r2.json()
            self.assertTrue(ready_body["ready"])
            self.assertTrue(ready_body["within_startup_budget"])

            allowed = await client.get("/api/ccc/devices")
            self.assertEqual(allowed.status, 200)
        finally:
            await client.close()


class PaginationCaps(unittest.TestCase):
    def test_device_list_page_max(self):
        from app.services.ccc_device_service import LIST_PAGE_MAX
        from app.services.ccc_historical_report_service import (
            CCC_REPORT_CSV_MAX,
            CCC_REPORT_PAGE_MAX,
        )

        self.assertEqual(LIST_PAGE_MAX, 200)
        self.assertEqual(CCC_REPORT_PAGE_MAX, 200)
        self.assertEqual(CCC_REPORT_CSV_MAX, 2000)


class ParallelResponsiveness(unittest.IsolatedAsyncioTestCase):
    async def test_many_health_calls_do_not_block(self):
        app = web.Application()
        state = new_startup_state()
        mark_startup_begin(state)
        mark_listen(state)
        mark_ready(state)
        app[STARTUP_KEY] = state
        app.router.add_get("/api/health", health_handler)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:

            async def one():
                resp = await client.get("/api/health")
                self.assertEqual(resp.status, 200)
                return await resp.json()

            results = await asyncio.gather(*[one() for _ in range(40)])
            self.assertEqual(len(results), 40)
            self.assertTrue(all(r["ready"] for r in results))
        finally:
            await client.close()


class TlsAndHttpsEnforce(unittest.IsolatedAsyncioTestCase):
    def test_tls_capability_min_128(self):
        cap = tls_capability_public()
        self.assertGreaterEqual(cap["min_symmetric_bits"], 128)
        self.assertIn("TLSv1.2", cap["preferred_tls_versions"])
        self.assertFalse(cap["aiohttp_terminates_tls"])
        self.assertTrue(cap["media_routing"]["relative_urls_only"])
        self.assertTrue(cap["media_routing"]["example_ws_path"].startswith("/media/"))

    def test_secure_cookie_under_https_forward(self):
        req = make_mocked_request("GET", "/api/login", headers={"X-Forwarded-Proto": "https"})
        kw = session_cookie_kwargs(req, max_age=3600)
        self.assertTrue(kw["httponly"])
        self.assertEqual(kw["samesite"], "Lax")
        self.assertTrue(kw["secure"])

    def test_insecure_allowed_when_enforce_off(self):
        req = make_mocked_request("GET", "/api/ccc/devices")
        with patch.dict(os.environ, {"HTTPS_ENFORCE": "0"}, clear=False):
            self.assertTrue(allow_insecure_request(req))

    async def test_enforce_rejects_remote_http(self):
        app = web.Application(middlewares=[https_enforce_middleware])

        async def ok(_r):
            return web.json_response({"ok": True})

        app.router.add_get("/api/ccc/devices", ok)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            with patch.dict(os.environ, {"HTTPS_ENFORCE": "1"}, clear=False):
                # TestClient uses 127.0.0.1 — loopback is allowed even under enforce
                local = await client.get("/api/ccc/devices")
                self.assertEqual(local.status, 200)

                # Simulate remote peer without HTTPS
                with patch(
                    "app.services.ccc_transport_security.is_loopback_client",
                    return_value=False,
                ):
                    denied = await client.get("/api/ccc/devices")
                    self.assertEqual(denied.status, 403)
                    data = await denied.json()
                    self.assertTrue(data["https_enforce"])
        finally:
            await client.close()

    def test_nginx_tls_sample_exists_with_modern_ciphers(self):
        from pathlib import Path

        sample = Path(__file__).resolve().parents[2] / "deploy" / "nginx-cctv-tls.sample.conf"
        self.assertTrue(sample.is_file())
        text = sample.read_text(encoding="utf-8")
        self.assertIn("listen 443 ssl", text)
        self.assertIn("TLSv1.2", text)
        self.assertIn("TLSv1.3", text)
        self.assertIn("AES128-GCM", text)
        self.assertIn("X-Forwarded-Proto https", text)


if __name__ == "__main__":
    unittest.main()
