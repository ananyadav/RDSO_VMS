"""Tests for /api/health and startup gating."""

import asyncio
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.core.startup_state import STARTUP_KEY, health_handler, new_startup_state, startup_middleware


class StartupHealthTests(unittest.TestCase):
    def test_health_not_ready_by_default(self):
        async def run():
            app = web.Application()
            app[STARTUP_KEY] = new_startup_state()
            app.router.add_get("/api/health", health_handler)
            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            try:
                resp = await client.get("/api/health")
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertFalse(data["ready"])
                self.assertFalse(data["mongodb"])
            finally:
                await client.close()

        asyncio.run(run())

    def test_startup_middleware_blocks_api_until_ready(self):
        async def run():
            app = web.Application(middlewares=[startup_middleware])
            app[STARTUP_KEY] = new_startup_state()

            async def ok_handler(_request):
                return web.json_response({"ok": True})

            app.router.add_get("/api/cameras", ok_handler)
            app.router.add_get("/api/health", health_handler)

            server = TestServer(app)
            client = TestClient(server)
            await client.start_server()
            try:
                blocked = await client.get("/api/cameras")
                self.assertEqual(blocked.status, 503)

                health = await client.get("/api/health")
                self.assertEqual(health.status, 200)

                app[STARTUP_KEY]["ready"] = True
                allowed = await client.get("/api/cameras")
                self.assertEqual(allowed.status, 200)
            finally:
                await client.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
