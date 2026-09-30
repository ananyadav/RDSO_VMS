"""RDSO 18.2.2 / 18.2.3 / 18.2.27 — network video transport tests."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.client_media_routing import build_client_media_routing, live_ws_path
from app.services.network_video_transport import (
    MulticastConfigError,
    build_multicast_ingest_url,
    is_ipv4_multicast_address,
    is_multicast_ingest_url,
    normalize_multicast_config,
    system_transport_status,
    transport_capability_public,
    validate_multicast_address,
)
from app.services.rtsp_utils import stream_source_urls


class LanWanRelativeRouting(unittest.TestCase):
    def test_live_ws_path_relative(self):
        path = live_ws_path(2)
        self.assertTrue(path.startswith("/media/w"))
        self.assertNotIn("://", path)
        self.assertNotIn("127.0.0.1", path)

    def test_client_media_no_secrets_or_hosts(self):
        routing = build_client_media_routing(
            {
                "_id": "abc",
                "camera_uid": "ip_1_2_3_4",
                "worker_id": 1,
                "password": "secret",
                "main_rtsp_url": "rtsp://admin:secret@10.0.0.1/stream",
            }
        )
        blob = str(routing)
        self.assertNotIn("secret", blob)
        self.assertNotIn("rtsp://", blob)
        self.assertTrue(routing["client_must_not_use_server_host"])
        self.assertTrue(routing["rdso_18_2_2"])
        self.assertTrue(routing["live"]["ws_path"].startswith("/"))
        self.assertEqual(routing["live"]["browser_transport"], "unicast")


class UnicastDefault(unittest.TestCase):
    def test_capability_unicast_default(self):
        cap = transport_capability_public()
        self.assertTrue(cap["unicast"]["supported"])
        self.assertTrue(cap["unicast"]["default"])
        self.assertTrue(cap["rdso_18_2_3"])

    def test_stream_sources_unicast_when_multicast_off(self):
        cam = {
            "protocol": "HIKVISION",
            "ip_address": "192.168.1.10",
            "username": "admin",
            "password": "x",
            "multicast": {"enabled": False},
        }
        urls = stream_source_urls(cam, main=False)
        self.assertTrue(urls)
        self.assertTrue(any(u.startswith("rtsp://") or u.startswith("ffmpeg:rtsp://") or "rtsp://" in u for u in urls))


class MulticastValidation(unittest.TestCase):
    def test_valid_range(self):
        self.assertTrue(is_ipv4_multicast_address("239.255.0.1"))
        self.assertTrue(is_ipv4_multicast_address("224.0.0.1"))
        self.assertFalse(is_ipv4_multicast_address("192.168.1.1"))
        self.assertFalse(is_ipv4_multicast_address("10.0.0.1"))
        self.assertEqual(validate_multicast_address("239.1.1.1"), "239.1.1.1")
        with self.assertRaises(MulticastConfigError):
            validate_multicast_address("8.8.8.8")

    def test_normalize_enabled(self):
        cfg = normalize_multicast_config(
            {"enabled": True, "address": "239.192.0.10", "port": 5000, "source_mode": "udp_mpegts"}
        )
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["address"], "239.192.0.10")
        url = build_multicast_ingest_url(cfg)
        self.assertIsNotNone(url)
        self.assertTrue(is_multicast_ingest_url(url or ""))
        self.assertIn("239.192.0.10", url or "")

    def test_invalid_port(self):
        with self.assertRaises(MulticastConfigError):
            normalize_multicast_config(
                {"enabled": True, "address": "239.0.0.1", "port": 99999, "source_mode": "rtp"}
            )


class MulticastSourceRouting(unittest.TestCase):
    def test_multicast_preferred_then_unicast_fallback(self):
        cam = {
            "protocol": "HIKVISION",
            "ip_address": "192.168.1.10",
            "username": "admin",
            "password": "x",
            "multicast": {
                "enabled": True,
                "address": "239.255.1.1",
                "port": 5004,
                "source_mode": "udp_mpegts",
            },
        }
        urls = stream_source_urls(cam, main=True)
        self.assertGreaterEqual(len(urls), 2)
        self.assertTrue(is_multicast_ingest_url(urls[0]))
        # Unicast fallback present
        self.assertTrue(any("192.168.1.10" in u or "rtsp://" in u for u in urls[1:]))


class UnsupportedFallback(unittest.TestCase):
    def test_browser_native_multicast_false(self):
        cap = transport_capability_public(
            camera={
                "multicast": {
                    "enabled": True,
                    "address": "239.1.1.1",
                    "port": 5004,
                    "source_mode": "rtp",
                }
            }
        )
        self.assertFalse(cap["multicast"]["browser_native_multicast"])
        self.assertEqual(cap["multicast"]["browser_delivery"], "vms_relayed_unicast")
        self.assertIn("network_acceptance_required", cap["multicast"])

    def test_rtsp_with_tcp_skips_multicast(self):
        from app.services.go2rtc_service import _rtsp_with_tcp

        mcast = "ffmpeg:udp://@239.1.1.1:5004#video=h264"
        self.assertEqual(_rtsp_with_tcp(mcast), mcast)
        uni = "rtsp://admin:x@10.0.0.1/stream"
        out = _rtsp_with_tcp(uni)
        self.assertIn("rtsp_transport=tcp", out)


class SystemTransportApi(unittest.TestCase):
    def test_system_status_flags(self):
        status = system_transport_status()
        self.assertTrue(status["rdso_18_2_2"])
        self.assertTrue(status["rdso_18_2_3"])
        self.assertTrue(status["rdso_18_2_27"])
        self.assertFalse(status["network"]["lan_only_assumption"])
        self.assertTrue(status["security"]["client_media_excludes_secrets"])


class TransportRouteRbac(unittest.IsolatedAsyncioTestCase):
    async def test_update_transport_requires_admin(self):
        from app.routes.cameras import update_camera_transport_endpoint

        req = make_mocked_request("PUT", "/api/cameras/x/transport", match_info={"id": "x"})
        with patch(
            "app.routes.cameras.require_admin",
            new=AsyncMock(side_effect=web.HTTPUnauthorized()),
        ):
            resp = await update_camera_transport_endpoint(req)
        self.assertEqual(resp.status, 401)

    async def test_get_transport_requires_acl(self):
        from app.routes.cameras import get_camera_transport_endpoint

        req = make_mocked_request("GET", "/api/cameras/x/transport", match_info={"id": "x"})
        with patch(
            "app.routes.cameras.deny_unless_camera_access",
            new=AsyncMock(return_value=web.json_response({"error": "Forbidden"}, status=403)),
        ):
            resp = await get_camera_transport_endpoint(req)
        self.assertEqual(resp.status, 403)


class LiveConfigTransport(unittest.TestCase):
    def test_live_config_includes_transport(self):
        from app.services.go2rtc_service import get_live_config

        cfg = get_live_config()
        self.assertTrue(cfg["transport"]["unicast_default"])
        self.assertFalse(cfg["transport"]["multicast_browser_native"])
        self.assertTrue(cfg["transport"]["relative_media_paths"])


if __name__ == "__main__":
    unittest.main()
