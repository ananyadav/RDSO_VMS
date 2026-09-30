"""RDSO 18.3.15 — ONVIF Profile S/G interoperability + integration API tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.edge_onvif_recording import parse_get_recordings, parse_replay_uri_response
from app.services.onvif_stream_uri import (
    STREAM_SOURCE_ONVIF,
    ensure_onvif_recording_urls,
    parse_stream_uri_response,
    should_resolve_onvif_for_recording,
)
from app.services.rtsp_utils import ensure_rtsp_credentials, mask_rtsp_url


STREAM_URI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
  xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body>
    <trt:GetStreamUriResponse>
      <trt:MediaUri>
        <tt:Uri>rtsp://192.168.1.10:554/Streaming/Channels/101</tt:Uri>
      </trt:MediaUri>
    </trt:GetStreamUriResponse>
  </s:Body>
</s:Envelope>
"""

REPLAY_URI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetReplayUriResponse>
      <Uri>rtsp://192.168.1.10:554/Streaming/tracks/101?starttime=2026-09-07T10:00:00Z</Uri>
    </GetReplayUriResponse>
  </s:Body>
</s:Envelope>
"""

PROFILES_XML = """
<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
  xmlns:tt="http://www.onvif.org/ver10/schema">
  <trt:Profiles token="Profile_1">
    <tt:Name>mainStream</tt:Name>
    <tt:VideoEncoderConfiguration token="VEC_Main">
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
      <tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit></tt:RateControl>
    </tt:VideoEncoderConfiguration>
  </trt:Profiles>
  <trt:Profiles token="Profile_2">
    <tt:Name>subStream</tt:Name>
    <tt:VideoEncoderConfiguration token="VEC_Sub">
      <tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
      <tt:RateControl><tt:FrameRateLimit>15</tt:FrameRateLimit></tt:RateControl>
    </tt:VideoEncoderConfiguration>
  </trt:Profiles>
</trt:GetProfilesResponse>
"""

ONVIF_RECORDINGS = """<?xml version="1.0" encoding="UTF-8"?>
<GetRecordingsResponse>
  <RecordingItem token="rec-1"><Name>Edge</Name></RecordingItem>
</GetRecordingsResponse>
"""


class ProfileSUriParsing(unittest.TestCase):
    def test_parse_stream_uri(self):
        uri = parse_stream_uri_response(STREAM_URI_XML)
        self.assertEqual(uri, "rtsp://192.168.1.10:554/Streaming/Channels/101")

    def test_inject_credentials(self):
        out = ensure_rtsp_credentials(
            "rtsp://192.168.1.10:554/Streaming/Channels/101",
            "admin",
            "p@ss",
        )
        self.assertIn("admin:", out)
        self.assertTrue(out.startswith("rtsp://"))
        self.assertIn("@192.168.1.10", out)
        self.assertEqual(mask_rtsp_url(out), "rtsp://192.168.1.10:554/Streaming/Channels/101")

    def test_should_resolve_onvif_only(self):
        self.assertTrue(should_resolve_onvif_for_recording({"protocol": "ONVIF"}))
        self.assertFalse(should_resolve_onvif_for_recording({"protocol": "HIKVISION"}))
        self.assertTrue(
            should_resolve_onvif_for_recording(
                {"protocol": "HIKVISION", "rtsp_url_source": STREAM_SOURCE_ONVIF}
            )
        )


class ProfileSResolveMocked(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_main_sub_uris(self):
        from app.services.onvif_stream_uri import resolve_onvif_profile_s_streams

        cam = {
            "_id": "abc",
            "username": "admin",
            "password": "secret",
            "ip_address": "192.168.1.10",
            "protocol": "ONVIF",
        }

        async def fake_profiles(camera):
            from app.services.onvif_media import parse_profiles_list

            return parse_profiles_list(PROFILES_XML), None

        async def fake_uri(camera, token, stream_protocol="RTSP"):
            if token == "Profile_1":
                return "rtsp://192.168.1.10:554/main", None
            if token == "Profile_2":
                return "rtsp://192.168.1.10:554/sub", None
            return None, "unknown"

        with patch(
            "app.services.onvif_stream_uri._get_profiles",
            side_effect=fake_profiles,
        ), patch(
            "app.services.onvif_stream_uri.get_onvif_stream_uri",
            side_effect=fake_uri,
        ):
            result = await resolve_onvif_profile_s_streams(cam)

        self.assertTrue(result["supported"])
        self.assertTrue(result["main"]["ok"])
        self.assertIn("admin:", result["main"]["rtsp_uri"])
        self.assertTrue(result["sub"]["ok"])
        self.assertEqual(result["source"], STREAM_SOURCE_ONVIF)

    async def test_recording_uses_onvif_resolved_stream(self):
        cam = {
            "protocol": "ONVIF",
            "username": "admin",
            "password": "secret",
            "ip_address": "192.168.1.10",
        }

        async def fake_resolve(camera):
            return {
                "supported": True,
                "message": "ok",
                "main": {
                    "ok": True,
                    "rtsp_uri": "rtsp://admin:secret@192.168.1.10/main",
                    "profile_token": "P1",
                },
                "sub": {
                    "ok": True,
                    "rtsp_uri": "rtsp://admin:secret@192.168.1.10/sub",
                    "profile_token": "P2",
                },
                "profiles": [],
            }

        with patch(
            "app.services.onvif_stream_uri.resolve_onvif_profile_s_streams",
            side_effect=fake_resolve,
        ):
            out = await ensure_onvif_recording_urls(cam)

        self.assertTrue(out["ok"])
        self.assertTrue(out["applied"])
        self.assertEqual(out["camera"]["rtsp_url_source"], STREAM_SOURCE_ONVIF)
        self.assertIn("/main", out["camera"]["main_rtsp_url"])

    async def test_unsupported_getstreamuri_honest(self):
        cam = {"protocol": "ONVIF", "username": "admin", "password": "x"}

        async def fail_resolve(camera):
            return {
                "supported": False,
                "message": "GetStreamUri not available",
                "main": {"ok": False, "error": "Fault"},
                "sub": None,
                "profiles": [],
            }

        with patch(
            "app.services.onvif_stream_uri.resolve_onvif_profile_s_streams",
            side_effect=fail_resolve,
        ):
            out = await ensure_onvif_recording_urls(cam)
        self.assertFalse(out["ok"])
        self.assertFalse(out["applied"])
        self.assertIn("not available", (out.get("message") or "").lower())


class ProfileGReplay(unittest.TestCase):
    def test_parse_replay_uri(self):
        uri = parse_replay_uri_response(REPLAY_URI_XML)
        self.assertTrue(uri.startswith("rtsp://"))

    def test_parse_recordings(self):
        recs = parse_get_recordings(ONVIF_RECORDINGS)
        self.assertEqual(recs[0]["token"], "rec-1")


class ProfileGUnsupportedDownload(unittest.IsolatedAsyncioTestCase):
    async def test_onvif_download_without_uri_raises(self):
        from pathlib import Path
        import tempfile

        from app.services.edge_ingest import download_edge_clip_to_file
        from app.services.edge_storage_types import PROTOCOL_ONVIF_G

        cam = {"username": "admin", "password": "x"}
        with patch(
            "app.services.edge_ingest.get_onvif_replay_uri",
            new=AsyncMock(return_value=(None, "GetReplayUri not available (HTTP 404)")),
        ):
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "clip.bin"
                with self.assertRaises(RuntimeError) as ctx:
                    await download_edge_clip_to_file(
                        cam, {"recording_token": "rec-1"}, dest, protocol=PROTOCOL_ONVIF_G
                    )
                self.assertIn("Replay", str(ctx.exception))


class VendorFallback(unittest.TestCase):
    def test_hikvision_does_not_force_onvif_resolve(self):
        self.assertFalse(
            should_resolve_onvif_for_recording(
                {
                    "protocol": "HIKVISION",
                    "main_rtsp_url": "rtsp://x",
                    "rtsp_url_source": "auto_hikvision",
                }
            )
        )

    def test_dahua_templates_still_auto(self):
        from app.services.rtsp_utils import build_rtsp_urls

        urls = build_rtsp_urls(
            make="DAHUA",
            ip="192.168.1.20",
            username="admin",
            password="x",
        )
        self.assertIn("realmonitor", urls["main_rtsp_url"])
        self.assertEqual(urls["rtsp_source"], "auto_dahua")


class CameraCredentialMasking(unittest.TestCase):
    def test_management_item_masks_password_and_rtsp(self):
        from bson import ObjectId

        from app.services.camera_service import _camera_management_item

        cam = {
            "_id": ObjectId(),
            "name": "Cam",
            "ip_address": "192.168.1.5",
            "username": "admin",
            "password": "supersecret",
            "protocol": "HIKVISION",
            "main_rtsp_url": "rtsp://admin:supersecret@192.168.1.5:554/Streaming/Channels/101",
            "sub_rtsp_url": "rtsp://admin:supersecret@192.168.1.5:554/Streaming/Channels/102",
            "is_active": True,
        }
        item = _camera_management_item(cam)
        self.assertEqual(item["password"], "***")
        self.assertNotIn("supersecret", item["main_rtsp_url"])
        self.assertNotIn("supersecret", item["sub_rtsp_url"])


class IntegrationDocs(unittest.TestCase):
    def test_docs_files_exist(self):
        docs = Path(__file__).resolve().parents[2] / "docs"
        self.assertTrue((docs / "integration-api.md").is_file(), docs)
        schema = json.loads((docs / "integration-openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["openapi"][:3], "3.0")
        self.assertIn("/api/cameras/{id}/onvif/profile-s", schema["paths"])


class ApiAuthRbac(unittest.IsolatedAsyncioTestCase):
    async def test_profile_s_requires_auth(self):
        from app.routes.onvif_interop import onvif_profile_s_endpoint

        req = make_mocked_request("GET", "/api/cameras/x/onvif/profile-s", match_info={"id": "x"})
        with patch(
            "app.routes.onvif_interop.deny_unless_admin",
            new=AsyncMock(return_value=web.json_response({"error": "Authentication required"}, status=401)),
        ):
            resp = await onvif_profile_s_endpoint(req)
        self.assertEqual(resp.status, 401)

    async def test_interop_requires_camera_acl(self):
        from app.routes.onvif_interop import camera_interop_endpoint

        req = make_mocked_request("GET", "/api/cameras/x/interop", match_info={"id": "x"})
        with patch(
            "app.routes.onvif_interop.deny_unless_admin",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.routes.onvif_interop.deny_unless_camera_access",
            new=AsyncMock(return_value=web.json_response({"error": "Forbidden"}, status=403)),
        ):
            resp = await camera_interop_endpoint(req)
        self.assertEqual(resp.status, 403)


class RecordingRegressionSmoke(unittest.TestCase):
    def test_resolve_recording_rtsp_still_uses_urls(self):
        from app.services.recording_config import resolve_recording_rtsp_url

        with patch.dict("os.environ", {"RECORDING_VIA_GO2RTC": "false"}, clear=False):
            cam = {"recording_channel": "main", "main_rtsp_url": "rtsp://a/main"}
            url, label = resolve_recording_rtsp_url(
                cam, {"main_rtsp_url": "rtsp://a/main", "sub_rtsp_url": "rtsp://a/sub"}
            )
            self.assertEqual(url, "rtsp://a/main")
            self.assertIn("main", label)


if __name__ == "__main__":
    unittest.main()
