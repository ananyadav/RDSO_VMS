"""RDSO 18.3.16 — edge storage failover & backfill tests (mocked cameras)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.edge_gap_detection import detect_recording_gaps, merge_edge_clips_into_gaps
from app.services.edge_isapi import build_cmsearch_xml, parse_cmsearch_response
from app.services.edge_onvif_recording import parse_get_recordings
from app.services.edge_storage_types import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    job_key,
    to_iso,
)
from app.services.recording_export import compute_gaps


CMSEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult>
  <searchMatchItem>
    <timeSpan>
      <startTime>2026-09-07T10:00:00Z</startTime>
      <endTime>2026-09-07T10:05:00Z</endTime>
    </timeSpan>
    <mediaSegmentDescriptor>
      <playbackURI>/ISAPI/ContentMgmt/download?playbackURI=clip1</playbackURI>
    </mediaSegmentDescriptor>
  </searchMatchItem>
</CMSearchResult>
"""

ONVIF_RECORDINGS = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <GetRecordingsResponse>
      <RecordingItem token="rec-1">
        <Name>Edge</Name>
      </RecordingItem>
    </GetRecordingsResponse>
  </s:Body>
</s:Envelope>
"""


class GapDetectionTests(unittest.TestCase):
    def test_compute_gaps_basic(self):
        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)
        pieces = [
            {
                "clipStart": "2026-09-07T10:10:00Z",
                "clipEnd": "2026-09-07T10:40:00Z",
            }
        ]
        gaps = compute_gaps(start, end, pieces)
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[0]["start"], "2026-09-07T10:00:00+00:00")
        self.assertTrue(gaps[0]["end"].startswith("2026-09-07T10:10:00"))

    def test_merge_edge_clips_partial(self):
        gaps = [
            {
                "start": "2026-09-07T10:00:00Z",
                "end": "2026-09-07T10:30:00Z",
                "reason": "no_footage",
            }
        ]
        clips = [
            {
                "start": "2026-09-07T10:05:00Z",
                "end": "2026-09-07T10:15:00Z",
                "playback_uri": "/x",
            }
        ]
        out = merge_edge_clips_into_gaps(gaps, clips)
        self.assertEqual(out[0]["edge_clip_count"], 1)
        self.assertEqual(out[0]["edge_clips"][0]["overlap_seconds"], 600.0)


class ParserTests(unittest.TestCase):
    def test_parse_cmsearch(self):
        clips = parse_cmsearch_response(CMSEARCH_XML)
        self.assertEqual(len(clips), 1)
        self.assertIn("10:00:00", clips[0]["start"])
        self.assertTrue(clips[0]["playback_uri"])

    def test_build_cmsearch_xml_contains_window(self):
        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)
        xml = build_cmsearch_xml(start=start, end=end, track_id="101").decode()
        self.assertIn("CMSearchDescription", xml)
        self.assertIn("2026-09-07T10:00:00", xml)

    def test_parse_onvif_recordings(self):
        recs = parse_get_recordings(ONVIF_RECORDINGS)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["token"], "rec-1")


class CapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_when_probes_fail(self):
        from app.services.edge_capability import detect_edge_storage_capability

        cam = {"protocol": "CUSTOM", "ip_address": "10.0.0.9", "password": "x"}
        with patch(
            "app.services.edge_capability.probe_onvif_profile_g",
            new_callable=AsyncMock,
            return_value={"supported": False, "message": "no g"},
        ), patch(
            "app.services.edge_capability.probe_isapi_edge_storage",
            new_callable=AsyncMock,
            return_value={"supported": False, "message": "no isapi"},
        ):
            cap = await detect_edge_storage_capability(cam)
        self.assertFalse(cap["supported"])
        self.assertIn("no", cap["message"].lower())

    async def test_hikvision_uses_isapi_when_supported(self):
        from app.services.edge_capability import detect_edge_storage_capability

        cam = {"protocol": "HIKVISION", "ip_address": "10.0.0.8", "password": "x"}
        with patch(
            "app.services.edge_capability.probe_isapi_edge_storage",
            new_callable=AsyncMock,
            return_value={
                "supported": True,
                "protocol": "hikvision_isapi",
                "search_supported": True,
                "storage_present": True,
                "message": "ok",
            },
        ):
            cap = await detect_edge_storage_capability(cam)
        self.assertTrue(cap["supported"])
        self.assertEqual(cap["protocol"], "hikvision_isapi")

    async def test_explicit_disabled(self):
        from app.services.edge_capability import detect_edge_storage_capability

        cap = await detect_edge_storage_capability(
            {"edge_storage_protocol": "none", "protocol": "HIKVISION"}
        )
        self.assertFalse(cap["supported"])


class GapServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_recording_gaps_from_sessions(self):
        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)

        async def fake_filter(_ref):
            return {"camera_id": "cam1"}

        docs = [
            {
                "_id": "s1",
                "started_at": "2026-09-07T10:00:00Z",
                "stopped_at": "2026-09-07T10:20:00Z",
                "status": "stopped",
                "source": "vms",
            }
        ]

        class FakeCursor:
            def __aiter__(self):
                self._i = 0
                return self

            async def __anext__(self):
                if self._i >= len(docs):
                    raise StopAsyncIteration
                d = docs[self._i]
                self._i += 1
                return d

        with patch(
            "app.services.edge_gap_detection.recording_session_mongo_filter",
            side_effect=fake_filter,
        ), patch(
            "app.services.edge_gap_detection.recording_sessions_collection"
        ) as coll:
            coll.find.return_value = FakeCursor()
            info = await detect_recording_gaps("cam1", start, end)
        self.assertEqual(info["gap_count"], 1)
        self.assertTrue(info["gaps"][0]["start"].startswith("2026-09-07T10:20:00"))


class BackfillFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_fabricated_recovery_when_unsupported(self):
        from app.services.edge_backfill_service import start_edge_backfill

        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)
        with patch(
            "app.services.edge_backfill_service._load_camera",
            new_callable=AsyncMock,
            return_value={"_id": "507f1f77bcf86cd799439011", "protocol": "CUSTOM"},
        ), patch(
            "app.services.edge_backfill_service.detect_edge_storage_capability",
            new_callable=AsyncMock,
            return_value={"supported": False, "message": "no edge"},
        ), patch(
            "app.services.edge_backfill_service.ensure_edge_backfill_indexes",
            new_callable=AsyncMock,
        ):
            out = await start_edge_backfill(
                "507f1f77bcf86cd799439011", start, end, confirm=True
            )
        self.assertFalse(out["ok"])
        self.assertFalse(out["edge_supported"])
        self.assertEqual(out["jobs"], [])

    async def test_requires_confirm(self):
        from app.services.edge_backfill_service import start_edge_backfill

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            await start_edge_backfill("cam", start, end, confirm=False)

    async def test_idempotent_active_job_not_duplicated(self):
        from app.services.edge_backfill_service import start_edge_backfill

        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 10, 30, tzinfo=timezone.utc)
        key = job_key("507f1f77bcf86cd799439011", start, end)
        with patch(
            "app.services.edge_backfill_service._load_camera",
            new_callable=AsyncMock,
            return_value={
                "_id": "507f1f77bcf86cd799439011",
                "protocol": "HIKVISION",
                "camera_uid": "ip_1",
            },
        ), patch(
            "app.services.edge_backfill_service.detect_edge_storage_capability",
            new_callable=AsyncMock,
            return_value={"supported": True, "protocol": "hikvision_isapi"},
        ), patch(
            "app.services.edge_backfill_service.detect_recording_gaps",
            new_callable=AsyncMock,
            return_value={
                "gaps": [{"start": to_iso(start), "end": to_iso(end)}],
            },
        ), patch(
            "app.services.edge_backfill_service.ensure_edge_backfill_indexes",
            new_callable=AsyncMock,
        ), patch(
            "app.services.edge_backfill_service.find_active_job_by_key",
            new_callable=AsyncMock,
            return_value={"_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "job_key": key, "status": JOB_RUNNING},
        ), patch(
            "app.services.edge_backfill_service.get_job",
            new_callable=AsyncMock,
            return_value={
                "id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                "status": JOB_RUNNING,
                "job_key": key,
            },
        ), patch(
            "app.services.edge_backfill_service.create_job",
            new_callable=AsyncMock,
        ) as create_job:
            out = await start_edge_backfill(
                "507f1f77bcf86cd799439011", start, end, confirm=True
            )
        create_job.assert_not_called()
        self.assertTrue(out["jobs"][0].get("idempotent_reuse"))

    async def test_successful_backfill_ingest(self):
        from app.services.edge_backfill_service import execute_backfill_job

        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 10, 5, tzinfo=timezone.utc)
        job = {
            "id": "bbbbbbbbbbbbbbbbbbbbbbbb",
            "camera_id": "507f1f77bcf86cd799439011",
            "gap_start": to_iso(start),
            "gap_end": to_iso(end),
            "protocol": "hikvision_isapi",
            "status": JOB_PENDING,
        }
        camera = {
            "_id": "507f1f77bcf86cd799439011",
            "id": "507f1f77bcf86cd799439011",
            "protocol": "HIKVISION",
            "ip_address": "192.168.41.31",
            "camera_uid": "ip_192_168_41_31",
            "name": "test",
            "password": "x",
        }
        updates: list[dict] = []

        async def _update(_id, patch):
            updates.append(patch)
            return {**job, **patch, "id": job["id"]}

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "edge.mp4"
            media.write_bytes(b"\x00" * 64)

            async def _download(cam, clip, dest, protocol=""):
                dest.write_bytes(media.read_bytes())
                return dest.stat().st_size

            async def _ingest(*_a, **kwargs):
                # Create playable session tree for playback assertion
                root = Path(tmp) / "Recordings"
                sid = "cccccccccccccccccccccccc"
                sdir = root / "ip_192_168_41_31" / "sessions" / sid
                sdir.mkdir(parents=True)
                (sdir / "index.m3u8").write_text("#EXTM3U\n#EXTINF:4,\nseg_00000.ts\n")
                (sdir / "seg_00000.ts").write_bytes(b"\x00" * 128)
                return {
                    "id": sid,
                    "storage_path": f"ip_192_168_41_31/sessions/{sid}",
                    "source": "edge_backfill",
                    "started_at": to_iso(start),
                    "stopped_at": to_iso(end),
                }

            with patch(
                "app.services.edge_backfill_service.get_job",
                new_callable=AsyncMock,
                side_effect=lambda _id: {**job, **(updates[-1] if updates else {}), "id": job["id"]},
            ), patch(
                "app.services.edge_backfill_service.update_job",
                side_effect=_update,
            ), patch(
                "app.services.edge_backfill_service.search_edge_clips",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "start": to_iso(start),
                        "end": to_iso(end),
                        "local_path": str(media),
                        "playback_uri": "/x",
                    }
                ],
            ), patch(
                "app.services.edge_backfill_service.download_edge_clip_to_file",
                side_effect=_download,
            ), patch(
                "app.services.edge_backfill_service.ingest_edge_media_as_session",
                side_effect=_ingest,
            ):
                result = await execute_backfill_job(job["id"], camera)

        self.assertEqual(result["status"], JOB_COMPLETED)
        self.assertEqual(len(result["session_ids"]), 1)

    async def test_partial_when_no_edge_clips(self):
        from app.services.edge_backfill_service import execute_backfill_job

        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 10, 5, tzinfo=timezone.utc)
        job = {
            "id": "dddddddddddddddddddddddd",
            "camera_id": "cam",
            "gap_start": to_iso(start),
            "gap_end": to_iso(end),
            "protocol": "hikvision_isapi",
        }
        updates = []

        async def _update(_id, patch):
            updates.append(patch)
            return {**job, **patch, "id": job["id"]}

        with patch(
            "app.services.edge_backfill_service.get_job",
            new_callable=AsyncMock,
            side_effect=lambda _id: {**job, **(updates[-1] if updates else {}), "id": job["id"]},
        ), patch(
            "app.services.edge_backfill_service.update_job",
            side_effect=_update,
        ), patch(
            "app.services.edge_backfill_service.search_edge_clips",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await execute_backfill_job(
                job["id"],
                {"_id": "cam", "protocol": "HIKVISION", "password": "x"},
            )
        self.assertEqual(result["status"], JOB_FAILED)
        self.assertIn("No edge footage", result["message"])


class RouteRbacTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_requires_super_admin(self):
        from app.routes.edge_backfill import edge_capability_endpoint

        req = make_mocked_request("GET", "/api/recordings/edge/capability/x")
        with patch(
            "app.routes.edge_backfill.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "forbidden"}, status=403),
        ):
            resp = await edge_capability_endpoint(req)
        self.assertEqual(resp.status, 403)

    async def test_backfill_ok_path(self):
        from app.routes.edge_backfill import edge_backfill_start_endpoint

        req = make_mocked_request("POST", "/api/recordings/edge/backfill")

        async def _json():
            return {
                "camera_id": "cam1",
                "from": "2026-09-07T10:00:00Z",
                "to": "2026-09-07T11:00:00Z",
                "confirm": True,
            }

        req.json = _json  # type: ignore
        with patch(
            "app.routes.edge_backfill.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.edge_backfill_service.start_edge_backfill",
            new_callable=AsyncMock,
            return_value={"ok": True, "jobs": []},
        ):
            resp = await edge_backfill_start_endpoint(req)
        self.assertEqual(resp.status, 200)


class PlaybackFindsEdgeSessionTests(unittest.TestCase):
    def test_edge_session_covered_in_gap_math(self):
        """After backfill, coverage pieces remove the gap (playback can find it)."""
        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)
        pieces = [
            {
                "clipStart": "2026-09-07T10:00:00Z",
                "clipEnd": "2026-09-07T11:00:00Z",
                "source": "edge_backfill",
            }
        ]
        gaps = compute_gaps(start, end, pieces)
        self.assertEqual(gaps, [])


if __name__ == "__main__":
    unittest.main()
