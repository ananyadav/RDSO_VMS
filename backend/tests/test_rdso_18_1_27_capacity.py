"""RDSO 18.1.27 — stream / monitor / priority / replay capacity (software)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.playback_search import MAX_MULTI_PLAYBACK_CAMERAS
from app.services.priority_levels import (
    PRIORITY_LEVELS,
    PRIORITY_MAX,
    PRIORITY_MIN,
    should_auto_display_alarm,
    sort_alarms_by_priority,
    normalize_priority,
    priority_from_severity,
)
from app.services.rdso_18_1_27_capacity import (
    RDSO_18_1_27_MIN_MONITORS,
    RDSO_18_1_27_MIN_PRIORITY_LEVELS,
    RDSO_18_1_27_MIN_REPLAY_CAMERAS,
    RDSO_18_1_27_MIN_VIDEO_STREAMS,
    get_rdso_18_1_27_capacity,
)
from app.services.recording_config import (
    RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS,
    get_recording_capacity_info,
)
from app.services.go2rtc_workers import MAX_CAMERAS_PER_WORKER, needed_workers_for_camera_count


class StreamCap(unittest.TestCase):
    def test_no_software_cap_below_128_by_default(self):
        info = get_recording_capacity_info()
        self.assertTrue(info["unlimited"] or info["max_concurrent_recordings"] >= 128)
        self.assertGreaterEqual(info["rdso_min_simultaneous_streams"], 128)
        self.assertTrue(info["rdso_18_3_5_software_compliant"])

    def test_go2rtc_workers_cover_128(self):
        self.assertGreaterEqual(MAX_CAMERAS_PER_WORKER, 128)
        self.assertEqual(needed_workers_for_camera_count(128), 1)
        self.assertGreaterEqual(RDSO_MIN_SIMULTANEOUS_RECORDING_STREAMS, 128)

    def test_soft_cap_below_128_flags_noncompliant(self):
        with patch.dict(os.environ, {"RECORDING_MAX_CONCURRENT": "32"}, clear=False):
            # recording_config reads at import — patch module constant
            import app.services.recording_config as rc

            prev = rc.RECORDING_MAX_CONCURRENT
            try:
                rc.RECORDING_MAX_CONCURRENT = 32
                info = rc.get_recording_capacity_info()
                self.assertFalse(info["rdso_18_3_5_software_compliant"])
            finally:
                rc.RECORDING_MAX_CONCURRENT = prev


class CapacityAggregate(unittest.TestCase):
    def test_18_1_27_capacity_snapshot(self):
        cap = get_rdso_18_1_27_capacity()
        self.assertTrue(cap["rdso_18_1_27"])
        self.assertEqual(cap["video_streams"]["min_required"], RDSO_18_1_27_MIN_VIDEO_STREAMS)
        self.assertEqual(cap["monitors"]["min_required"], RDSO_18_1_27_MIN_MONITORS)
        self.assertEqual(cap["monitors"]["software_monitor_identities"], 8)
        self.assertGreaterEqual(len(cap["priority"]["levels"]), RDSO_18_1_27_MIN_PRIORITY_LEVELS)
        self.assertGreaterEqual(
            cap["replay"]["max_multi_playback_cameras"], RDSO_18_1_27_MIN_REPLAY_CAMERAS
        )
        self.assertTrue(cap["software_compliant"])
        self.assertTrue(cap["priority"]["policy"]["distinct_from_rbac"])


class PriorityLevels(unittest.TestCase):
    def test_five_levels(self):
        self.assertEqual(list(PRIORITY_LEVELS), [1, 2, 3, 4, 5])
        self.assertEqual(PRIORITY_MIN, 1)
        self.assertEqual(PRIORITY_MAX, 5)

    def test_severity_defaults(self):
        self.assertEqual(priority_from_severity("critical"), 5)
        self.assertEqual(priority_from_severity("warning"), 3)
        self.assertEqual(priority_from_severity("info"), 1)

    def test_sort_priority_then_severity_then_time(self):
        events = [
            {"priority": 3, "severity": "critical", "occurred_at": "2026-01-01T10:00:00"},
            {"priority": 5, "severity": "info", "occurred_at": "2026-01-01T09:00:00"},
            {"priority": 5, "severity": "warning", "occurred_at": "2026-01-01T11:00:00"},
        ]
        ordered = sort_alarms_by_priority(events)
        self.assertEqual(ordered[0]["priority"], 5)
        self.assertEqual(ordered[0]["severity"], "warning")
        self.assertEqual(ordered[1]["priority"], 5)
        self.assertEqual(ordered[2]["priority"], 3)

    def test_user_alarm_conflict_rule(self):
        self.assertTrue(should_auto_display_alarm(alarm_priority=3, user_priority=3))
        self.assertTrue(should_auto_display_alarm(alarm_priority=5, user_priority=3))
        self.assertFalse(should_auto_display_alarm(alarm_priority=2, user_priority=4))

    def test_normalize_rejects_out_of_range(self):
        with self.assertRaises(Exception):
            normalize_priority(0)
        with self.assertRaises(Exception):
            normalize_priority(6)


class ReplaySixteen(unittest.TestCase):
    def test_max_multi_playback_at_least_16(self):
        self.assertGreaterEqual(MAX_MULTI_PLAYBACK_CAMERAS, 16)


class RbacDistinct(unittest.TestCase):
    def test_roles_unchanged_by_priority_module(self):
        from app.core.roles import ROLE_ADMIN, ROLE_OPERATOR, ROLE_SUPER_ADMIN, ROLE_VIEWER

        roles = {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}
        self.assertEqual(len(roles), 4)
        # Priority levels are numeric, not roles
        self.assertNotIn(5, roles)


if __name__ == "__main__":
    unittest.main()
