"""RDSO 18.2.28 — alarm display switch config on rules / events."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services.alarm_rule_service import AlarmRuleValidationError, _validate_display_config
from app.services.alarm_rule_evaluator import _execute_rule_actions
from app.services.alarm_signal import NormalizedAlarmSignal


class DisplaySwitchValidation(unittest.TestCase):
    def test_layout_switch_requires_ui_notification(self):
        with self.assertRaises(AlarmRuleValidationError):
            _validate_display_config(
                {"display": {"mode": "layout_switch", "layout": "2x2", "slot": 0}},
                actions=["create_event"],
            )

    def test_valid_layout_switch(self):
        cfg = _validate_display_config(
            {
                "display": {
                    "mode": "layout_switch",
                    "monitor_id": 2,
                    "layout": "4x4",
                    "slot": 3,
                    "restore_on_reset": True,
                }
            },
            actions=["create_event", "ui_notification"],
        )
        self.assertEqual(cfg["mode"], "layout_switch")
        self.assertEqual(cfg["monitor_id"], 2)
        self.assertEqual(cfg["layout"], "4x4")
        self.assertEqual(cfg["slot"], 3)

    def test_slot_out_of_range(self):
        with self.assertRaises(AlarmRuleValidationError):
            _validate_display_config(
                {"display": {"mode": "layout_switch", "layout": "2x2", "slot": 9}},
                actions=["ui_notification"],
            )

    def test_empty_display_clears(self):
        self.assertIsNone(_validate_display_config({"display": None}, actions=["ui_notification"]))


class DisplaySwitchOnEvent(unittest.IsolatedAsyncioTestCase):
    async def test_event_metadata_carries_display_switch(self):
        cam_id = "507f1f77bcf86cd799439011"
        rule = {
            "_id": ObjectId(),
            "actions": ["ui_notification"],
            "severity": "warning",
            "priority": 4,
            "display": {
                "mode": "layout_switch",
                "monitor_id": 1,
                "layout": "2x2",
                "slot": 1,
                "restore_on_reset": True,
            },
        }
        signal = NormalizedAlarmSignal(
            camera_id=cam_id,
            camera_uid="uid-1",
            source_type="motion",
            title="Motion",
            message="motion",
            occurred_at=datetime(2026, 9, 10, 5, 0, 0, tzinfo=timezone.utc),
            metadata={"edge": True},
        )
        created = {}

        async def _create_event(**kwargs):
            created.update(kwargs)
            return {"id": "eid-1"}

        with patch(
            "app.services.alarm_rule_evaluator.create_event",
            new=AsyncMock(side_effect=_create_event),
        ), patch(
            "app.services.alarm_rule_evaluator.alarm_rules_collection"
        ) as col:
            col.update_one = AsyncMock()
            await _execute_rule_actions(rule, signal, camera_uid="uid-1")

        md = created.get("metadata") or {}
        self.assertIn("display_switch", md)
        self.assertEqual(md["display_switch"]["layout"], "2x2")
        self.assertEqual(md["display_switch"]["slot"], 1)
        self.assertEqual(md["display_switch"]["camera_id"], cam_id)
        self.assertTrue(md.get("edge"))


if __name__ == "__main__":
    unittest.main()
