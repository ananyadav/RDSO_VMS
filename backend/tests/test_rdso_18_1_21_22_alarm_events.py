"""RDSO 18.1.21 / 18.1.22 — signal loss + camera event inputs."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.alarm_signal import AlarmSignalValidationError, normalize_alarm_signal
from app.services.camera_event_inputs import (
    emit_camera_event_on_rising_edge,
    is_rising_edge,
    note_activity_state,
    parse_io_input_active,
    parse_io_input_port_nums,
    probe_digital_input_capability,
    reset_camera_event_input_state_for_tests,
)
from app.services.stream_health_alarm_adapter import (
    handle_stream_health_transition,
    is_signal_loss_transition,
)

CAMERA_ID = "507f1f77bcf86cd799439011"
CAMERA = {
    "_id": ObjectId(CAMERA_ID),
    "camera_uid": "ip_192_168_41_90",
    "ip_address": "192.168.41.90",
    "protocol": "HIKVISION",
}


class RisingEdgeHelpers(unittest.TestCase):
    def setUp(self):
        reset_camera_event_input_state_for_tests()

    def test_rising_edge_once(self):
        self.assertTrue(is_rising_edge(None, True))
        self.assertTrue(is_rising_edge(False, True))
        self.assertFalse(is_rising_edge(True, True))
        self.assertFalse(is_rising_edge(True, False))

    def test_note_state_tracks_previous(self):
        self.assertIsNone(note_activity_state(CAMERA_ID, "motion", True))
        self.assertTrue(note_activity_state(CAMERA_ID, "motion", True))
        self.assertTrue(note_activity_state(CAMERA_ID, "motion", False))


class SignalLoss1821(unittest.IsolatedAsyncioTestCase):
    def test_once_per_outage(self):
        alarm = {"ok": False, "alarm": True, "message": "down"}
        self.assertTrue(is_signal_loss_transition(False, alarm))
        self.assertFalse(is_signal_loss_transition(True, alarm))

    async def test_recovery_then_later_loss_new_event(self):
        healthy = {"ok": True, "alarm": False, "message": ""}
        alarm = {
            "ok": False,
            "alarm": True,
            "message": "timeout",
            "strikes": 3,
            "category": "timeout",
            "checkedAt": "2026-09-08T10:00:00+00:00",
        }
        with patch(
            "app.services.stream_health_alarm_adapter.mark_signal_loss_events_recovered",
            new=AsyncMock(return_value={"recovered": True, "matched": 1, "modified": 1}),
        ) as recovered:
            out = await handle_stream_health_transition(
                CAMERA, previous_alarm=True, current_result=healthy
            )
            self.assertTrue(out["recovery"]["recovered"])
            recovered.assert_awaited()

        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new=AsyncMock(return_value={"ok": True, "matched_rules": 1}),
        ) as proc:
            out2 = await handle_stream_health_transition(
                CAMERA, previous_alarm=False, current_result=alarm
            )
            self.assertTrue(out2.get("ok"))
            proc.assert_awaited()


class MotionDigitalInput1822(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_camera_event_input_state_for_tests()

    async def test_motion_event_rising_edge(self):
        with patch(
            "app.services.alarm_rule_evaluator.process_alarm_signal",
            new=AsyncMock(return_value={"ok": True, "matched_rules": 1, "events": [{"id": "e1"}]}),
        ) as proc:
            first = await emit_camera_event_on_rising_edge(
                CAMERA,
                source_type="motion",
                active=True,
                title="Motion detected",
                message="active",
            )
            second = await emit_camera_event_on_rising_edge(
                CAMERA,
                source_type="motion",
                active=True,
                title="Motion detected",
                message="active",
            )
        self.assertTrue(first["emitted"])
        self.assertFalse(second["emitted"])
        self.assertEqual(proc.await_count, 1)

    async def test_digital_input_event(self):
        with patch(
            "app.services.alarm_rule_evaluator.process_alarm_signal",
            new=AsyncMock(return_value={"ok": True, "matched_rules": 1}),
        ) as proc:
            out = await emit_camera_event_on_rising_edge(
                CAMERA,
                source_type="digital_input",
                active=True,
                title="Digital input / relay event",
                message="port 1",
                metadata={"port": 1, "relay": True},
            )
        self.assertTrue(out["emitted"])
        signal = proc.await_args.args[0]
        self.assertEqual(signal["source_type"], "digital_input")

    async def test_unsupported_digital_input_capability(self):
        with patch(
            "app.services.motion_capability._prefer_isapi",
            return_value=True,
        ), patch(
            "app.services.hikvision_ptz._isapi",
            new=AsyncMock(return_value=(200, "<IOCap><IOInputPortNums>0</IOInputPortNums></IOCap>")),
        ):
            cap = await probe_digital_input_capability(CAMERA)
        self.assertFalse(cap["supported"])
        self.assertIn("zero", cap["message"].lower())

    def test_unsupported_source_type(self):
        with self.assertRaises(AlarmSignalValidationError):
            normalize_alarm_signal(
                {
                    "camera_id": CAMERA_ID,
                    "source_type": "ai_face",
                    "title": "x",
                    "message": "y",
                }
            )

    def test_relay_alias_maps_to_digital_input(self):
        sig = normalize_alarm_signal(
            {
                "camera_id": CAMERA_ID,
                "source_type": "relay",
                "title": "Relay",
                "message": "active",
            }
        )
        self.assertEqual(sig.source_type, "digital_input")

    def test_parse_io_helpers(self):
        self.assertEqual(parse_io_input_port_nums("<IOCap><IOInputPortNums>2</IOInputPortNums></IOCap>"), 2)
        self.assertTrue(parse_io_input_active("<IOInputPortStatus><ioState>active</ioState></IOInputPortStatus>"))
        self.assertFalse(parse_io_input_active("<IOInputPortStatus><ioState>inactive</ioState></IOInputPortStatus>"))


class ActionsAndCooldown(unittest.IsolatedAsyncioTestCase):
    async def test_configured_action_execution_path(self):
        """Rising-edge emit reaches process_alarm_signal (actions executed inside evaluator)."""
        reset_camera_event_input_state_for_tests()
        with patch(
            "app.services.alarm_rule_evaluator.process_alarm_signal",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "matched_rules": 1,
                    "actions": ["create_event", "ui_notification", "start_recording"],
                }
            ),
        ) as proc:
            out = await emit_camera_event_on_rising_edge(
                CAMERA,
                source_type="signal_loss",
                active=True,
                title="Camera signal lost",
                message="offline",
            )
        self.assertTrue(out["emitted"])
        self.assertIn("create_event", out["result"]["actions"])

    async def test_cooldown_second_edge_suppressed_by_rising_gate(self):
        reset_camera_event_input_state_for_tests()
        with patch(
            "app.services.alarm_rule_evaluator.process_alarm_signal",
            new=AsyncMock(return_value={"ok": True}),
        ) as proc:
            await emit_camera_event_on_rising_edge(
                CAMERA, source_type="motion", active=True, title="M", message="m"
            )
            await emit_camera_event_on_rising_edge(
                CAMERA, source_type="motion", active=True, title="M", message="m"
            )
            # clear then rise again → new emit (recurrence after recovery)
            await emit_camera_event_on_rising_edge(
                CAMERA, source_type="motion", active=False, title="M", message="m"
            )
            await emit_camera_event_on_rising_edge(
                CAMERA, source_type="motion", active=True, title="M", message="m"
            )
        self.assertEqual(proc.await_count, 2)


class AclRbacSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_events_route_requires_events_permission(self):
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from app.routes.events import list_events_endpoint

        req = make_mocked_request("GET", "/api/events")
        with patch(
            "app.routes.events.deny_unless_events_permission",
            new=AsyncMock(return_value=web.json_response({"error": "Forbidden"}, status=403)),
        ):
            resp = await list_events_endpoint(req)
        self.assertEqual(resp.status, 403)


if __name__ == "__main__":
    unittest.main()
