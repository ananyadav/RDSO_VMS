"""RDSO 18.3.14 closure — bitrate validation + transition timing."""

from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from app.services.motion_bitrate_validation import (
    COMPLIANT,
    NON_COMPLIANT,
    UNKNOWN,
    compare_idle_active_profiles,
    estimate_segment_bitrate_kbps,
)
from app.services.motion_recording_controller import (
    clear_motion_state,
    get_activity_state,
    refresh_camera_config,
)
from app.services.stream_profile_service import parse_bitrate_kbps, parse_encoder_fields


def _block(profile: str, *, br: int | None, w: int = 640, h: int = 360, supported=True):
    return {
        "profile": profile,
        "channel": "101" if profile == "main" else "102",
        "supported": supported,
        "current": {
            "bitrate_kbps": br,
            "width": w,
            "height": h,
            "fps": 15,
            "codec": "H.264",
            "resolution": f"{w}x{h}",
        },
    }


class BitrateParseTests(unittest.TestCase):
    def test_parse_cbr_and_vbr(self):
        fields = parse_encoder_fields(
            "<StreamingChannel><constantBitRate>4096</constantBitRate>"
            "<vbrUpperCap>512</vbrUpperCap>"
            "<videoQualityControlType>CBR</videoQualityControlType></StreamingChannel>"
        )
        self.assertEqual(parse_bitrate_kbps(fields), 4096)
        fields2 = parse_encoder_fields(
            "<x><videoQualityControlType>VBR</videoQualityControlType>"
            "<vbrUpperCap>768</vbrUpperCap><constantBitRate>4096</constantBitRate></x>"
        )
        self.assertEqual(parse_bitrate_kbps(fields2), 768)


class CompareProfilesTests(unittest.TestCase):
    def test_idle_lower_is_compliant(self):
        out = compare_idle_active_profiles(
            _block("sub", br=512, w=640, h=360),
            _block("main", br=4096, w=1920, h=1080),
        )
        self.assertEqual(out["compliance"], COMPLIANT)
        self.assertEqual(out["idle_bitrate_kbps"], 512)
        self.assertEqual(out["active_bitrate_kbps"], 4096)

    def test_unknown_when_bitrate_missing(self):
        out = compare_idle_active_profiles(
            _block("sub", br=None),
            _block("main", br=4096),
        )
        self.assertEqual(out["compliance"], UNKNOWN)
        self.assertFalse(out["bitrate_known"])
        self.assertIn("unknown", out["message"].lower())

    def test_non_compliant_when_idle_not_lower(self):
        out = compare_idle_active_profiles(
            _block("sub", br=5000),
            _block("main", br=2000),
        )
        self.assertEqual(out["compliance"], NON_COMPLIANT)

    def test_does_not_claim_from_labels_alone(self):
        # Both supported but no bitrates — unknown even if sub vs main
        out = compare_idle_active_profiles(
            _block("sub", br=None, w=640, h=360),
            _block("main", br=None, w=1920, h=1080),
        )
        self.assertEqual(out["compliance"], UNKNOWN)


class EnableRejectsNonCompliant(unittest.IsolatedAsyncioTestCase):
    async def test_enable_blocked_when_idle_bitrate_ge_active(self):
        from app.services.motion_recording_config import update_motion_recording_settings

        with patch(
            "app.services.motion_recording_config.camera_collection"
        ) as coll, patch(
            "app.services.motion_recording_config.detect_motion_capability",
            new_callable=AsyncMock,
            return_value={"supported": True, "message": "ok"},
        ), patch(
            "app.services.motion_bitrate_validation.evaluate_motion_bitrate_compliance",
            new_callable=AsyncMock,
            return_value={
                "compliance": NON_COMPLIANT,
                "message": "Idle bitrate 5000 kbps is not lower than active 2000 kbps",
            },
        ):
            coll.find_one = AsyncMock(return_value={"_id": "507f1f77bcf86cd799439011"})
            with self.assertRaises(ValueError) as ctx:
                await update_motion_recording_settings(
                    "507f1f77bcf86cd799439011",
                    {"enabled": True},
                )
            self.assertIn("not lower", str(ctx.exception).lower())


class TransitionTimingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_motion_state()

    def tearDown(self):
        clear_motion_state()

    async def test_switch_records_elapsed_ms(self):
        from app.services import motion_recording_controller as ctl

        await refresh_camera_config(
            "cam1",
            {
                "enabled": True,
                "active_stream": "main",
                "idle_stream": "sub",
                "hold_seconds": 5,
                "cooldown_seconds": 1,
            },
        )
        ctl._STATE["cam1"]["current_stream"] = "sub"
        ctl._STATE["cam1"]["desired_stream"] = "main"
        ctl._STATE["cam1"]["last_switch_at"] = 0

        async def _stop(_cid):
            time.sleep(0.01)

        async def _start(_cid):
            time.sleep(0.01)
            return {"id": "s"}

        with patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.video_recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.video_recording.ACTIVE_RECORDINGS",
            {},
        ), patch(
            "app.services.video_recording.stop_camera_recording",
            side_effect=_stop,
        ), patch(
            "app.services.video_recording.start_camera_recording",
            side_effect=_start,
        ):
            ok = await ctl._maybe_switch_stream("cam1")
        self.assertTrue(ok)
        tr = get_activity_state("cam1")["last_transition"]
        self.assertIsNotNone(tr)
        self.assertEqual(tr["direction"], "quiet_to_motion")
        self.assertGreaterEqual(tr["elapsed_ms"], 10)
        self.assertLess(tr["elapsed_ms"], 5000)

    async def test_duplicate_prevention(self):
        from app.services import motion_recording_controller as ctl

        await refresh_camera_config(
            "cam1",
            {
                "enabled": True,
                "active_stream": "main",
                "idle_stream": "sub",
                "hold_seconds": 5,
                "cooldown_seconds": 1,
            },
        )
        ctl._STATE["cam1"]["current_stream"] = "sub"
        ctl._STATE["cam1"]["desired_stream"] = "main"
        ctl._STATE["cam1"]["last_switch_at"] = 0

        class StickyDict(dict):
            def pop(self, *a, **k):
                return self.get(a[0]) if a else None

        occupied = StickyDict({"cam1": {"session_id": "ghost"}})

        with patch(
            "app.services.recording_config.is_recording_engine_enabled",
            return_value=True,
        ), patch(
            "app.services.video_recording.is_camera_recording",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.services.video_recording.ACTIVE_RECORDINGS",
            occupied,
        ), patch(
            "app.services.video_recording.stop_camera_recording",
            new_callable=AsyncMock,
        ), patch(
            "app.services.video_recording.start_camera_recording",
            new_callable=AsyncMock,
        ) as start:
            ok = await ctl._maybe_switch_stream("cam1")
        self.assertFalse(ok)
        start.assert_not_called()
        self.assertEqual(get_activity_state("cam1")["last_error"], "duplicate_recorder_prevented")


class SegmentBitrateEstimateTests(unittest.TestCase):
    def test_estimate_from_segments(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 100_000 bytes * 8 / 4s = 200 kbps per segment assumption
            (d / "seg_00000.ts").write_bytes(b"\x00" * 100_000)
            (d / "seg_00001.ts").write_bytes(b"\x00" * 100_000)
            kbps = estimate_segment_bitrate_kbps(d, sample=2)
            self.assertIsNotNone(kbps)
            self.assertGreater(kbps, 0)


if __name__ == "__main__":
    unittest.main()
