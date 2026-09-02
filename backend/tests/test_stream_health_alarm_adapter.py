import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services.stream_health import _persist_health, reset_stream_health_for_tests
from app.services.stream_health_alarm_adapter import (
    build_signal_loss_signal,
    current_confirmed_alarm,
    handle_stream_health_transition,
    is_signal_loss_transition,
    notify_stream_health_alarm_transition,
    previous_alarm_from_camera,
)

CAMERA_ID = "507f1f77bcf86cd799439011"
RULE_ID = "507f1f77bcf86cd799439013"
EVENT_ID = "507f1f77bcf86cd799439015"

CAMERA = {
    "_id": ObjectId(CAMERA_ID),
    "camera_uid": "ip_192_168_41_106",
    "ip_address": "192.168.41.106",
    "stream_health_alarm": False,
}

CONFIRMED_ALARM_RESULT = {
    "cameraId": CAMERA_ID,
    "cameraUid": "ip_192_168_41_106",
    "ok": False,
    "alarm": True,
    "strikes": 3,
    "suspect": False,
    "category": "timeout",
    "message": "Stream probe timed out",
    "checkedAt": "2026-09-01T10:00:00+00:00",
}

SUSPECT_RESULT = {
    **CONFIRMED_ALARM_RESULT,
    "alarm": False,
    "suspect": True,
    "strikes": 1,
}

HEALTHY_RESULT = {
    "cameraId": CAMERA_ID,
    "cameraUid": "ip_192_168_41_106",
    "ok": True,
    "alarm": False,
    "strikes": 0,
    "category": "online",
    "message": "",
    "checkedAt": "2026-09-01T10:05:00+00:00",
}


class TestTransitionDetection(unittest.TestCase):
    def test_healthy_to_confirmed_offline_is_transition(self):
        self.assertTrue(is_signal_loss_transition(False, CONFIRMED_ALARM_RESULT))

    def test_offline_to_offline_not_transition(self):
        self.assertFalse(is_signal_loss_transition(True, CONFIRMED_ALARM_RESULT))

    def test_healthy_to_healthy_not_transition(self):
        self.assertFalse(is_signal_loss_transition(False, HEALTHY_RESULT))

    def test_offline_to_healthy_not_transition(self):
        self.assertFalse(is_signal_loss_transition(True, HEALTHY_RESULT))

    def test_suspect_not_confirmed_alarm(self):
        self.assertFalse(current_confirmed_alarm(SUSPECT_RESULT))
        self.assertFalse(is_signal_loss_transition(False, SUSPECT_RESULT))

    def test_previous_from_persisted_camera_doc(self):
        cam = {**CAMERA, "stream_health_alarm": True}
        self.assertTrue(previous_alarm_from_camera(cam))
        self.assertFalse(previous_alarm_from_camera({**CAMERA, "stream_health_alarm": False}))


class TestSignalContent(unittest.TestCase):
    def test_builds_safe_signal(self):
        signal = build_signal_loss_signal(CAMERA, CONFIRMED_ALARM_RESULT)
        self.assertEqual(signal["camera_id"], CAMERA_ID)
        self.assertEqual(signal["source_type"], "signal_loss")
        self.assertEqual(signal["title"], "Camera signal lost")
        self.assertIn("timed out", signal["message"])
        self.assertEqual(signal["metadata"]["health_category"], "timeout")
        self.assertEqual(signal["metadata"]["strikes"], 3)

    def test_rtsp_credentials_redacted_from_message(self):
        result = {
            **CONFIRMED_ALARM_RESULT,
            "message": "failed rtsp://admin:secret@192.168.1.1/stream",
        }
        signal = build_signal_loss_signal(CAMERA, result)
        self.assertNotIn("secret", signal["message"])
        self.assertNotIn("admin:secret", signal["message"])


class TestHandleStreamHealthTransition(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_to_offline_calls_evaluator_once(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
            return_value={"matched_rules": 1, "events_created": [EVENT_ID]},
        ) as proc:
            out = await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=CONFIRMED_ALARM_RESULT,
            )
        proc.assert_awaited_once()
        self.assertEqual(out["events_created"], [EVENT_ID])

    async def test_offline_to_offline_skips_evaluator(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            out = await handle_stream_health_transition(
                CAMERA,
                previous_alarm=True,
                current_result=CONFIRMED_ALARM_RESULT,
            )
        proc.assert_not_awaited()
        self.assertIsNone(out)

    async def test_healthy_to_healthy_skips_evaluator(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=HEALTHY_RESULT,
            )
        proc.assert_not_awaited()

    async def test_recovery_no_signal_loss(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            await handle_stream_health_transition(
                {**CAMERA, "stream_health_alarm": True},
                previous_alarm=True,
                current_result=HEALTHY_RESULT,
            )
        proc.assert_not_awaited()

    async def test_recurrence_after_recovery(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
            return_value={"matched_rules": 1},
        ) as proc:
            await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=CONFIRMED_ALARM_RESULT,
            )
            await handle_stream_health_transition(
                {**CAMERA, "stream_health_alarm": True},
                previous_alarm=True,
                current_result=CONFIRMED_ALARM_RESULT,
            )
            await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result={
                    **CONFIRMED_ALARM_RESULT,
                    "checkedAt": "2026-09-01T12:00:00+00:00",
                },
            )
        self.assertEqual(proc.await_count, 2)

    async def test_transient_failure_no_signal(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=SUSPECT_RESULT,
            )
        proc.assert_not_awaited()

    async def test_evaluator_failure_does_not_raise(self):
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            out = await notify_stream_health_alarm_transition(
                CAMERA,
                previous_alarm=False,
                result=CONFIRMED_ALARM_RESULT,
            )
        self.assertIsNone(out)

    async def test_evaluator_timeout_does_not_raise(self):
        async def _slow(**kwargs):
            await asyncio.sleep(5)
            return {}

        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            side_effect=_slow,
        ), patch(
            "app.services.stream_health_alarm_adapter.ALARM_PROCESS_TIMEOUT_SECONDS",
            0.05,
        ):
            out = await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=CONFIRMED_ALARM_RESULT,
            )
        self.assertIsNone(out)

    async def test_restart_safety_already_offline(self):
        cam = {**CAMERA, "stream_health_alarm": True}
        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            await handle_stream_health_transition(
                cam,
                previous_alarm=True,
                current_result=CONFIRMED_ALARM_RESULT,
            )
        proc.assert_not_awaited()


class TestPersistHealthIntegration(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        reset_stream_health_for_tests()

    async def test_persist_then_adapter_with_previous_state(self):
        camera = {
            "_id": ObjectId(CAMERA_ID),
            "camera_uid": "ip_192_168_41_106",
            "stream_health_alarm": False,
        }
        persisted = {"updated": False}

        class FakeCollection:
            async def update_one(self, filt, update):
                persisted["updated"] = True
                persisted["alarm"] = update["$set"]["stream_health_alarm"]

        with patch("app.services.stream_health.camera_collection", FakeCollection()), patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
            return_value={"matched_rules": 0},
        ) as proc:
            await _persist_health(camera, CONFIRMED_ALARM_RESULT)

        self.assertTrue(persisted["updated"])
        self.assertTrue(persisted["alarm"])
        proc.assert_awaited_once()

    async def test_suspect_not_persisted_or_evaluated(self):
        camera = {"_id": ObjectId(CAMERA_ID), "stream_health_alarm": False}
        persisted = {"called": False}

        class FakeCollection:
            async def update_one(self, filt, update):
                persisted["called"] = True

        with patch("app.services.stream_health.camera_collection", FakeCollection()), patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
        ) as proc:
            await _persist_health(camera, SUSPECT_RESULT)

        self.assertFalse(persisted["called"])
        proc.assert_not_awaited()

    async def test_alarm_failure_still_persisted(self):
        camera = {"_id": ObjectId(CAMERA_ID), "stream_health_alarm": False}
        persisted = {"called": False}

        class FakeCollection:
            async def update_one(self, filt, update):
                persisted["called"] = True

        with patch("app.services.stream_health.camera_collection", FakeCollection()), patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            new_callable=AsyncMock,
            side_effect=RuntimeError("evaluator down"),
        ):
            await _persist_health(camera, CONFIRMED_ALARM_RESULT)

        self.assertTrue(persisted["called"])


class TestEndToEndWithEvaluator(unittest.IsolatedAsyncioTestCase):
    """Adapter → evaluator with mocked Mongo for rules/events."""

    async def test_matching_rule_creates_event(self):
        from app.services.alarm_rule_evaluator import process_alarm_signal

        rule_store = {}

        class FakeRules:
            def find(self, query):
                matches = [r for r in rule_store.values() if r.get("enabled")]

                class _C:
                    def __init__(self, items):
                        self._items = items
                        self._i = 0

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        if self._i >= len(self._items):
                            raise StopAsyncIteration
                        x = self._items[self._i]
                        self._i += 1
                        return x

                return _C(matches)

            async def find_one_and_update(self, filt, update, return_document=None):
                rid = filt["_id"]
                rule = rule_store.get(rid)
                if not rule:
                    return None
                before = dict(rule)
                rule.setdefault("runtime", {})["last_triggered_at"] = update["$set"][
                    "runtime.last_triggered_at"
                ]
                rule["runtime"]["trigger_count"] = int(rule["runtime"].get("trigger_count") or 0) + 1
                return before

            async def update_one(self, filt, update):
                rid = filt["_id"]
                if rid in rule_store:
                    rule_store[rid].setdefault("runtime", {})[
                        "last_event_id"
                    ] = update["$set"]["runtime.last_event_id"]

        rule_store[ObjectId(RULE_ID)] = {
            "_id": ObjectId(RULE_ID),
            "enabled": True,
            "camera_id": CAMERA_ID,
            "trigger": {"source_type": "signal_loss"},
            "actions": ["create_event", "ui_notification"],
            "severity": "warning",
            "cooldown_seconds": 60,
            "runtime": {"last_triggered_at": None, "trigger_count": 0},
        }

        camera_doc = {
            "_id": ObjectId(CAMERA_ID),
            "camera_uid": "ip_192_168_41_106",
            "ip_address": "192.168.41.106",
        }

        with patch(
            "app.services.stream_health_alarm_adapter.process_alarm_signal",
            wraps=process_alarm_signal,
        ), patch(
            "app.services.alarm_rule_evaluator.alarm_rules_collection",
            FakeRules(),
        ), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=camera_doc,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value={
                "id": EVENT_ID,
                "camera_id": CAMERA_ID,
                "rule_id": RULE_ID,
                "source_type": "signal_loss",
                "ui_notification": True,
            },
        ) as create_mock:
            out = await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=CONFIRMED_ALARM_RESULT,
            )

        self.assertEqual(out["triggered_rules"], 1)
        create_mock.assert_awaited_once()

    async def test_no_matching_rule_no_event(self):
        with patch(
            "app.services.alarm_rule_evaluator.alarm_rules_collection"
        ) as rules_col, patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value={"_id": ObjectId(CAMERA_ID), "camera_uid": "ip_x"},
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
        ) as create_mock:

            class _C:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            rules_col.find.return_value = _C()

            out = await handle_stream_health_transition(
                CAMERA,
                previous_alarm=False,
                current_result=CONFIRMED_ALARM_RESULT,
            )

        self.assertEqual(out["matched_rules"], 0)
        create_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
