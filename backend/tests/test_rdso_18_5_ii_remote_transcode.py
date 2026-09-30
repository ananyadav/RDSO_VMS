"""RDSO 18.5(ii) — remote bandwidth-adaptive transcoding."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ffmpeg_util import ffmpeg_bin
from app.services.remote_transcode_profiles import (
    DIRECT_PATH_BANDWIDTH_KBPS,
    get_profile,
    resolve_transcode_intent,
    select_profile_for_bandwidth,
    remote_transcode_capability_public,
)
from app.services.remote_transcode_service import (
    build_ffmpeg_transcode_cmd,
    cleanup_idle_jobs,
    probe_media,
    reset_jobs_for_tests,
    resolve_live_input_url,
    run_file_transcode_for_acceptance,
    start_transcode_job,
    stop_transcode_job,
)


def _ffmpeg_available() -> bool:
    try:
        subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, check=True, timeout=10)
        return True
    except Exception:
        return False


class AutoBandwidthProfiles(unittest.TestCase):
    def test_auto_selects_lower_profile_for_thin_bandwidth(self):
        low = select_profile_for_bandwidth(400)
        self.assertEqual(low.id, "low")
        very = select_profile_for_bandwidth(100)
        self.assertEqual(very.id, "very_low")
        med = select_profile_for_bandwidth(1000)
        self.assertEqual(med.id, "medium")

    def test_auto_high_bandwidth_recommends_direct_lan_path(self):
        intent = resolve_transcode_intent(mode="auto", bandwidth_kbps=DIRECT_PATH_BANDWIDTH_KBPS)
        self.assertFalse(intent["transcode"])
        self.assertTrue(intent["recommend_direct"])

    def test_direct_mode_never_starts_transcode(self):
        intent = resolve_transcode_intent(mode="direct")
        self.assertFalse(intent["transcode"])

    def test_explicit_profile(self):
        intent = resolve_transcode_intent(mode="profile", profile_id="high")
        self.assertTrue(intent["transcode"])
        self.assertEqual(intent["profile"].id, "high")

    def test_capability_flags(self):
        cap = remote_transcode_capability_public()
        self.assertTrue(cap["rdso_18_5_ii"])
        self.assertTrue(cap["on_demand_only"])
        self.assertFalse(cap["fleet_wide_transcoding"])
        self.assertTrue(cap["lan_direct_unchanged"])
        self.assertEqual(cap["live_source"], "go2rtc_local_rtsp")
        self.assertTrue(cap["never_direct_camera_rtsp"])


class LiveSourceThroughGo2rtc(unittest.TestCase):
    def test_live_input_is_go2rtc_local_not_camera(self):
        url = resolve_live_input_url("ip_10_0_0_5", stream="main", worker_id=1)
        self.assertIn("127.0.0.1", url)
        self.assertIn("ip_10_0_0_5_main", url)
        # No embedded credentials
        host = url.split("://", 1)[-1].split("/", 1)[0]
        self.assertNotIn("@", host)

    def test_ffmpeg_cmd_reencodes_fps_scale_bitrate(self):
        profile = get_profile("low")
        assert profile is not None
        cmd = build_ffmpeg_transcode_cmd(
            input_url="rtsp://127.0.0.1:8554/cam_main",
            output_playlist=Path("out/index.m3u8"),
            profile=profile,
            segment_pattern="out/seg_%05d.ts",
        )
        joined = " ".join(cmd)
        self.assertIn("libx264", joined)
        self.assertIn(f"fps={profile.fps}", joined)
        self.assertIn(f"scale={profile.width}:{profile.height}", joined)
        self.assertIn(f"{profile.video_bitrate_kbps}k", joined)
        self.assertNotIn("-c:v copy", joined)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg not available")
class RealTranscodeAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_real_transcoded_output_changes_fps_resolution_codec(self):
        profile = get_profile("low")
        assert profile is not None
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            out = Path(tmp) / "out"
            # 1280x720@25 → expect ~640x360@10 h264
            subprocess.check_call(
                [
                    ffmpeg_bin(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=1280x720:rate=25",
                    "-t",
                    "3",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    "2500k",
                    str(src),
                ]
            )
            result = await run_file_transcode_for_acceptance(src, profile, out, duration_sec=2.5)
            self.assertTrue(result.get("ok"), result)
            probe = result["probe"]
            self.assertIn(probe.get("codec"), ("h264", "avc1"))
            self.assertLessEqual(probe["height"], profile.height + 16)
            self.assertLessEqual(probe["width"], profile.width + 16)
            self.assertGreater(probe["height"], 0)
            # FPS near profile (HLS/TS may report slightly off)
            self.assertLessEqual(probe["fps"], profile.fps + 2)
            self.assertGreaterEqual(probe["fps"], max(1.0, profile.fps - 3))
            if probe.get("bit_rate"):
                # Should be far below source 2.5 Mbps
                self.assertLess(probe["bit_rate"], 1_500_000)

    async def test_playback_style_file_input_transcodes(self):
        """Playback path uses recording files — same encoder, file input."""
        profile = get_profile("very_low")
        assert profile is not None
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "session.mp4"
            out = Path(tmp) / "tout"
            subprocess.check_call(
                [
                    ffmpeg_bin(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=960x540:rate=20",
                    "-t",
                    "2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ]
            )
            result = await run_file_transcode_for_acceptance(src, profile, out, duration_sec=2.0)
            self.assertTrue(result["ok"], result)
            self.assertGreaterEqual(result["segment_count"], 1)
            self.assertEqual(result["probe"]["codec"], "h264")
            self.assertLessEqual(result["probe"]["height"], profile.height + 8)


class JobCleanupAndAcl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_jobs_for_tests()

    async def asyncTearDown(self):
        reset_jobs_for_tests()

    async def test_cleanup_removes_temp_media(self):
        profile = get_profile("very_low")
        assert profile is not None
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "app.services.remote_transcode_service.remote_transcode_root",
                return_value=Path(tmp),
            ):
                with patch(
                    "app.services.remote_transcode_service.asyncio.create_subprocess_exec",
                    new_callable=AsyncMock,
                ) as spawn:
                    proc = MagicMock()
                    proc.returncode = None
                    proc.stderr = None
                    proc.terminate = MagicMock()
                    proc.kill = MagicMock()
                    proc.wait = AsyncMock(return_value=0)
                    spawn.return_value = proc
                    job = await start_transcode_job(
                        kind="live",
                        camera_id="cam1",
                        camera_uid="ip_1",
                        user_id="user1",
                        profile=profile,
                        input_url="rtsp://127.0.0.1:8554/ip_1_sub",
                        mode="auto",
                    )
                    out = job.output_dir
                    self.assertTrue(out.is_dir())
                    (out / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
                    ok = await stop_transcode_job(job.job_id)
                    self.assertTrue(ok)
                    self.assertFalse(out.exists())

    async def test_auth_rbac_route_helpers_imported(self):
        # Smoke: route module wires deny_unless_camera_access + live/playback gates.
        from app.routes import remote_web as rw

        self.assertTrue(callable(rw.remote_transcode_start_endpoint))
        self.assertTrue(callable(rw.remote_web_capacity_endpoint))


class LanRegression(unittest.TestCase):
    def test_client_media_routing_still_relative_go2rtc(self):
        from app.services.client_media_routing import build_client_media_routing

        routing = build_client_media_routing(
            {"_id": "abc", "camera_uid": "ip_1", "worker_id": 1}
        )
        self.assertTrue(routing["live"]["ws_path"].startswith("/media/w"))
        self.assertIn("playback", routing)
        self.assertTrue(routing["network"]["relative_urls_only"])


if __name__ == "__main__":
    unittest.main()
