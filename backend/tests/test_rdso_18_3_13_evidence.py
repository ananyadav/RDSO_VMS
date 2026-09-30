"""RDSO 18.3.13 — evidence integrity (SHA-256 manifests)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from app.services.evidence_integrity import (
    MANIFEST_FILENAME,
    VERIFY_MISSING,
    VERIFY_MODIFIED,
    VERIFY_VALID,
    create_evidence_manifest,
    export_file_integrity,
    load_manifest,
    verify_evidence_manifest,
)


def _make_session_dir(root: Path) -> Path:
    d = root / "ip_test" / "sessions" / "sess1"
    d.mkdir(parents=True)
    (d / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg_00000.ts\n",
        encoding="utf-8",
    )
    (d / "seg_00000.ts").write_bytes(b"\x00FAKESEGMENTDATA\x01" * 32)
    (d / "seg_00001.ts").write_bytes(b"\x00MORESEGMENTDATA\x02" * 32)
    return d


class ManifestLifecycleTests(unittest.TestCase):
    def test_manifest_created_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            session = {
                "id": "sess1",
                "camera_id": "cam1",
                "camera_uid": "ip_test",
                "camera_name": "Test",
                "started_at": "2026-09-07T10:00:00Z",
                "stopped_at": "2026-09-07T10:05:00Z",
                "source": "vms",
            }
            result = create_evidence_manifest(session_dir, session)
            self.assertTrue(result["ok"])
            self.assertTrue(result["created"])
            self.assertTrue((session_dir / MANIFEST_FILENAME).is_file())
            man = load_manifest(session_dir)
            self.assertEqual(man["algorithm"], "SHA-256")
            self.assertGreaterEqual(man["file_count"], 3)
            self.assertIn("manifest_sha256", man)
            self.assertIn("#EXT-X-ENDLIST", (session_dir / "index.m3u8").read_text())

            verify = verify_evidence_manifest(session_dir)
            self.assertEqual(verify["status"], VERIFY_VALID)
            self.assertTrue(verify["valid"])

    def test_does_not_silently_regenerate(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            session = {"id": "sess1", "camera_id": "cam1", "started_at": "t0", "stopped_at": "t1"}
            first = create_evidence_manifest(session_dir, session)
            digest = first["manifest"]["files"][0]["sha256"]
            # Tamper then attempt non-force create — must keep original hashes
            (session_dir / "seg_00000.ts").write_bytes(b"TAMPERED")
            second = create_evidence_manifest(session_dir, session, force=False)
            self.assertFalse(second["created"])
            self.assertEqual(second["reason"], "manifest_already_exists")
            self.assertEqual(second["manifest"]["files"][0]["sha256"], digest)
            verify = verify_evidence_manifest(session_dir)
            self.assertEqual(verify["status"], VERIFY_MODIFIED)

    def test_modified_segment_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            create_evidence_manifest(
                session_dir,
                {"id": "sess1", "camera_id": "c", "started_at": "a", "stopped_at": "b"},
            )
            (session_dir / "seg_00001.ts").write_bytes(b"CHANGED")
            result = verify_evidence_manifest(session_dir)
            self.assertEqual(result["status"], VERIFY_MODIFIED)
            self.assertFalse(result["valid"])
            self.assertGreaterEqual(result["modified_count"], 1)

    def test_missing_segment_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            create_evidence_manifest(
                session_dir,
                {"id": "sess1", "camera_id": "c", "started_at": "a", "stopped_at": "b"},
            )
            (session_dir / "seg_00001.ts").unlink()
            result = verify_evidence_manifest(session_dir)
            self.assertEqual(result["status"], VERIFY_MISSING)
            self.assertFalse(result["valid"])

    def test_export_integrity_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "cam.mp4"
            f.write_bytes(b"fake-mp4-bytes")
            block = export_file_integrity(f)
            self.assertEqual(block["algorithm"], "SHA-256")
            self.assertEqual(len(block["sha256"]), 64)

    def test_edge_backfill_source_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            result = create_evidence_manifest(
                session_dir,
                {
                    "id": "sess1",
                    "camera_id": "c",
                    "source": "edge_backfill",
                    "started_at": "a",
                    "stopped_at": "b",
                },
            )
            self.assertEqual(result["manifest"]["source"], "edge_backfill")
            self.assertTrue(verify_evidence_manifest(session_dir)["valid"])


class ExportArchiveEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_zip_includes_evidence_metadata(self):
        from app.services.recording_export import build_export_archive
        import zipfile
        from datetime import datetime, timezone

        start = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 7, 10, 5, tzinfo=timezone.utc)

        async def fake_export(ref, rs, re, work_dir):
            out = work_dir / "ip_cam.mp4"
            out.write_bytes(b"exported-clip")
            from app.services.evidence_integrity import export_file_integrity

            return {
                "cameraId": "c1",
                "cameraUid": "ip_cam",
                "cameraName": "Cam",
                "ok": True,
                "filename": "ip_cam.mp4",
                "exportedSeconds": 5,
                "gaps": [],
                "pieces": 1,
                "remux": "copy",
                "integrity": export_file_integrity(out),
                "sourceEvidence": [],
            }

        with patch(
            "app.services.recording_export.export_camera_interval",
            side_effect=fake_export,
        ):
            payload, report = await build_export_archive(["ip_cam"], start, end)

        self.assertIn("evidence", report)
        self.assertEqual(report["evidence"]["algorithm"], "SHA-256")
        self.assertGreaterEqual(len(report["evidence"]["files"]), 1)
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "e.zip"
            zpath.write_bytes(payload)
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                self.assertIn("report.json", names)
                self.assertIn("evidence_integrity.json", names)
                self.assertIn("cameras/ip_cam.mp4", names)
                evidence = json.loads(zf.read("evidence_integrity.json"))
                self.assertEqual(evidence["files"][0]["sha256"], report["evidence"]["files"][0]["sha256"])


class EvidenceRouteRbacTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_requires_playback_permission(self):
        from app.routes.recording import session_evidence_verify_endpoint

        req = make_mocked_request("POST", "/api/recordings/sessions/x/evidence/verify")
        with patch(
            "app.routes.recording.deny_unless_playback_permission",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "forbidden"}, status=403),
        ):
            resp = await session_evidence_verify_endpoint(req)
        self.assertEqual(resp.status, 403)

    async def test_force_regenerate_requires_super_admin(self):
        from app.routes.recording import session_evidence_seal_endpoint

        req = make_mocked_request("POST", "/api/recordings/sessions/x/evidence/seal")

        async def _json():
            return {"force": True, "confirm": True}

        req.json = _json  # type: ignore
        with patch(
            "app.routes.recording.deny_unless_super_admin",
            new_callable=AsyncMock,
            return_value=web.json_response({"error": "forbidden"}, status=403),
        ):
            resp = await session_evidence_seal_endpoint(req)
        self.assertEqual(resp.status, 403)


class FinalizeHooksEvidence(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_creates_manifest(self):
        from app.services.video_recording import _finalize_recording_session

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = _make_session_dir(Path(tmp))
            sess = {
                "id": "sess1",
                "camera_id": "cam1",
                "camera_uid": "ip_test",
                "storage_path": "ip_test/sessions/sess1",
                "started_at": "2026-09-07T10:00:00Z",
                "stopped_at": "2026-09-07T10:05:00Z",
            }
            with patch(
                "app.services.video_recording.session_dir_for_folder",
                return_value=session_dir,
            ), patch(
                "app.services.video_recording.update_recording_session",
                new_callable=AsyncMock,
                return_value=sess,
            ), patch(
                "app.core.database.get_recording_session",
                new_callable=AsyncMock,
                return_value=sess,
            ), patch(
                "app.core.database.update_recording_session",
                new_callable=AsyncMock,
                return_value=sess,
            ):
                await _finalize_recording_session(
                    "cam1",
                    "sess1",
                    stop_reason="test",
                    storage_folder="ip_test",
                )
            self.assertTrue((session_dir / MANIFEST_FILENAME).is_file())
            self.assertTrue(verify_evidence_manifest(session_dir)["valid"])


if __name__ == "__main__":
    unittest.main()
