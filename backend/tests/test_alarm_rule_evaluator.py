import asyncio
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services.alarm_rule_evaluator import (
    process_alarm_signal,
    process_test_alarm_signal,
    try_claim_rule_execution,
)
from app.services.alarm_signal import AlarmSignalValidationError, normalize_alarm_signal

CAMERA_ID = "507f1f77bcf86cd799439011"
OTHER_CAMERA_ID = "507f1f77bcf86cd799439012"
RULE_ID = "507f1f77bcf86cd799439013"
RULE_ID_B = "507f1f77bcf86cd799439014"
EVENT_ID = "507f1f77bcf86cd799439015"

CAMERA_DOC = {
    "_id": ObjectId(CAMERA_ID),
    "camera_uid": "ip_192_168_41_106",
    "ip_address": "192.168.41.106",
}

BASE_SIGNAL = {
    "camera_id": CAMERA_ID,
    "camera_uid": "ip_192_168_41_106",
    "source_type": "signal_loss",
    "occurred_at": "2026-09-01T10:00:00+00:00",
    "title": "Camera signal lost",
    "message": "No video frame received",
    "metadata": {"probe": "timeout"},
}

ENABLED_RULE = {
    "_id": ObjectId(RULE_ID),
    "name": "Signal loss",
    "enabled": True,
    "camera_id": CAMERA_ID,
    "trigger": {"source_type": "signal_loss"},
    "actions": ["create_event", "ui_notification"],
    "severity": "warning",
    "cooldown_seconds": 60,
    "runtime": {"last_triggered_at": None, "last_event_id": None, "trigger_count": 0},
}

UI_ONLY_RULE = {
    **ENABLED_RULE,
    "_id": ObjectId(RULE_ID_B),
    "actions": ["ui_notification"],
}


class FakeAlarmRulesStore:
    """Minimal in-memory store simulating atomic find_one_and_update claims."""

    def __init__(self, rules: list[dict]):
        self.rules = {rule["_id"]: deepcopy(rule) for rule in rules}

    def find(self, query: dict):
        matches = []
        for rule in self.rules.values():
            if not query.get("enabled", True) and rule.get("enabled"):
                continue
            if query.get("enabled") and not rule.get("enabled"):
                continue
            if rule.get("camera_id") != query.get("camera_id"):
                continue
            if (rule.get("trigger") or {}).get("source_type") != query.get("trigger.source_type"):
                continue
            matches.append(deepcopy(rule))

        class _Cursor:
            def __init__(self, items):
                self._items = items
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._idx]
                self._idx += 1
                return item

        return _Cursor(matches)

    async def find_one_and_update(self, filt, update, return_document=None):
        rid = filt["_id"]
        rule = self.rules.get(rid)
        if not rule or not rule.get("enabled"):
            return None

        runtime = rule.setdefault("runtime", {"last_triggered_at": None, "trigger_count": 0})
        cooldown = int(rule.get("cooldown_seconds") or 0)
        now_iso = update["$set"]["runtime.last_triggered_at"]

        if cooldown > 0:
            last = runtime.get("last_triggered_at")
            if last:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                if (now_dt - last_dt).total_seconds() < cooldown:
                    return None

        before = deepcopy(rule)
        runtime["last_triggered_at"] = now_iso
        runtime["trigger_count"] = int(runtime.get("trigger_count") or 0) + 1
        return before

    async def update_one(self, filt, update):
        rid = filt["_id"]
        if rid not in self.rules:
            return
        for key, value in update.get("$set", {}).items():
            if key.startswith("runtime."):
                self.rules[rid].setdefault("runtime", {})[key.split(".", 1)[1]] = value


def _created_event(**overrides):
    base = {
        "id": EVENT_ID,
        "camera_id": CAMERA_ID,
        "camera_uid": "ip_192_168_41_106",
        "rule_id": RULE_ID,
        "source_type": "signal_loss",
        "severity": "warning",
        "title": BASE_SIGNAL["title"],
        "message": BASE_SIGNAL["message"],
        "occurred_at": BASE_SIGNAL["occurred_at"],
        "status": "open",
        "acknowledged": False,
        "actions_triggered": ["create_event", "ui_notification"],
        "ui_notification": True,
        "metadata": {"probe": "timeout"},
    }
    base.update(overrides)
    return base


class TestAlarmRuleEvaluator(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_matching_rule_creates_event(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value=_created_event(),
        ) as create_mock:
            result = await process_alarm_signal(BASE_SIGNAL)

        self.assertEqual(result["matched_rules"], 1)
        self.assertEqual(result["triggered_rules"], 1)
        self.assertEqual(result["events_created"], [EVENT_ID])
        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs["camera_id"], CAMERA_ID)
        self.assertEqual(kwargs["rule_id"], RULE_ID)
        self.assertTrue(kwargs["ui_notification"])

    async def test_different_camera_no_match(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        signal = {**BASE_SIGNAL, "camera_id": OTHER_CAMERA_ID}
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value={**CAMERA_DOC, "_id": ObjectId(OTHER_CAMERA_ID)},
        ):
            result = await process_alarm_signal(signal)
        self.assertEqual(result["matched_rules"], 0)

    async def test_different_source_type_no_match(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        signal = {**BASE_SIGNAL, "source_type": "motion"}
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ):
            result = await process_alarm_signal(signal)
        self.assertEqual(result["matched_rules"], 0)

    async def test_disabled_rule_not_matched(self):
        disabled = {**ENABLED_RULE, "enabled": False}
        store = FakeAlarmRulesStore([disabled])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ):
            result = await process_alarm_signal(BASE_SIGNAL)
        self.assertEqual(result["matched_rules"], 0)

    async def test_cooldown_suppresses_second_signal(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value=_created_event(),
        ) as create_mock:
            first = await process_alarm_signal(BASE_SIGNAL)
            second = await process_alarm_signal(BASE_SIGNAL)

        self.assertEqual(first["triggered_rules"], 1)
        self.assertEqual(second["triggered_rules"], 0)
        self.assertEqual(second["suppressed_rules"], 1)
        self.assertEqual(create_mock.await_count, 1)

    async def test_signal_after_cooldown_creates_new_event(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        later = {
            **BASE_SIGNAL,
            "occurred_at": datetime(2026, 9, 1, 10, 2, 0, tzinfo=timezone.utc).isoformat(),
        }
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            side_effect=[_created_event(), _created_event(id="evt2")],
        ) as create_mock:
            await process_alarm_signal(BASE_SIGNAL)
            result = await process_alarm_signal(later)

        self.assertEqual(result["triggered_rules"], 1)
        self.assertEqual(create_mock.await_count, 2)

    async def test_cooldown_state_persisted_in_store(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value=_created_event(),
        ):
            await process_alarm_signal(BASE_SIGNAL)

        runtime = store.rules[ObjectId(RULE_ID)]["runtime"]
        self.assertIsNotNone(runtime.get("last_triggered_at"))
        self.assertEqual(runtime.get("trigger_count"), 1)

    async def test_concurrent_signals_only_one_trigger(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        lock = asyncio.Lock()

        async def _slow_create(**kwargs):
            async with lock:
                await asyncio.sleep(0.05)
                return _created_event()

        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            side_effect=_slow_create,
        ):
            results = await asyncio.gather(
                process_alarm_signal(BASE_SIGNAL),
                process_alarm_signal(BASE_SIGNAL),
            )

        self.assertEqual(sum(r["triggered_rules"] for r in results), 1)
        self.assertEqual(sum(r["suppressed_rules"] for r in results), 1)

    async def test_multiple_rules_both_execute(self):
        rule_b = deepcopy(ENABLED_RULE)
        rule_b["_id"] = ObjectId("507f1f77bcf86cd799439016")
        store = FakeAlarmRulesStore([ENABLED_RULE, rule_b])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            side_effect=[
                _created_event(id="e1"),
                _created_event(id="e2"),
            ],
        ):
            result = await process_alarm_signal(BASE_SIGNAL)

        self.assertEqual(result["matched_rules"], 2)
        self.assertEqual(result["triggered_rules"], 2)

    async def test_failure_isolation(self):
        rule_b = deepcopy(ENABLED_RULE)
        rule_b["_id"] = ObjectId("507f1f77bcf86cd799439017")
        store = FakeAlarmRulesStore([ENABLED_RULE, rule_b])

        async def _create_side_effect(**kwargs):
            if kwargs.get("rule_id") == RULE_ID:
                raise RuntimeError("boom")
            return _created_event(id="ok")

        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            side_effect=_create_side_effect,
        ):
            result = await process_alarm_signal(BASE_SIGNAL)

        self.assertEqual(result["failed_rules"], 1)
        self.assertEqual(result["triggered_rules"], 1)
        self.assertEqual(result["events_created"], ["ok"])

    async def test_ui_notification_only_still_persists_event(self):
        store = FakeAlarmRulesStore([UI_ONLY_RULE])
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value=_created_event(
                actions_triggered=["ui_notification"],
                ui_notification=True,
                rule_id=RULE_ID_B,
            ),
        ) as create_mock:
            result = await process_alarm_signal(BASE_SIGNAL)

        self.assertEqual(result["triggered_rules"], 1)
        kwargs = create_mock.await_args.kwargs
        self.assertEqual(kwargs["actions_triggered"], ["ui_notification"])
        self.assertTrue(kwargs["ui_notification"])

    async def test_secret_metadata_redacted(self):
        signal = {**BASE_SIGNAL, "metadata": {"password": "secret", "note": "ok"}}
        normalized = normalize_alarm_signal(signal)
        self.assertEqual(normalized.metadata["password"], "[REDACTED]")

    async def test_process_test_alarm_signal_helper(self):
        with patch(
            "app.services.alarm_rule_evaluator.process_alarm_signal",
            new_callable=AsyncMock,
            return_value={"matched_rules": 0},
        ) as proc:
            await process_test_alarm_signal(BASE_SIGNAL)
        proc.assert_awaited_once()

    async def test_try_claim_persists_runtime(self):
        store = FakeAlarmRulesStore([ENABLED_RULE])
        now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store):
            ok = await try_claim_rule_execution(ENABLED_RULE, now=now)
            blocked = await try_claim_rule_execution(
                ENABLED_RULE,
                now=datetime(2026, 9, 1, 10, 0, 20, tzinfo=timezone.utc),
            )
        self.assertTrue(ok)
        self.assertFalse(blocked)

    async def test_start_recording_action_updates_event(self):
        rule = {
            **ENABLED_RULE,
            "actions": ["create_event", "start_recording"],
            "recording": {"duration_seconds": 30},
        }
        store = FakeAlarmRulesStore([rule])
        fake_event = {
            "id": EVENT_ID,
            "camera_id": CAMERA_ID,
            "actions_triggered": ["create_event", "start_recording"],
            "ui_notification": False,
        }
        with patch("app.services.alarm_rule_evaluator.alarm_rules_collection", store), patch(
            "app.services.alarm_rule_evaluator.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value=CAMERA_DOC,
        ), patch(
            "app.services.alarm_rule_evaluator.create_event",
            new_callable=AsyncMock,
            return_value=fake_event,
        ), patch(
            "app.services.alarm_rule_evaluator.start_alarm_triggered_recording",
            new_callable=AsyncMock,
            return_value={"recording_status": "started", "recording_session_id": "sess123"},
        ) as mock_rec, patch(
            "app.services.alarm_rule_evaluator.update_event_recording_result",
            new_callable=AsyncMock,
        ) as mock_update:
            result = await process_alarm_signal(BASE_SIGNAL)
        self.assertEqual(result["triggered_rules"], 1)
        mock_rec.assert_awaited_once()
        mock_update.assert_awaited_once()
        self.assertEqual(mock_update.await_args.kwargs["recording_status"], "started")


class TestAlarmSignalValidation(unittest.TestCase):
    def test_missing_camera_id(self):
        with self.assertRaises(AlarmSignalValidationError):
            normalize_alarm_signal({"source_type": "signal_loss", "title": "x", "message": "y"})


class TestAlarmEvaluatorSecurity(unittest.TestCase):
    def test_no_public_event_create_or_alarm_signal_route(self):
        from aiohttp import web

        from app.routes.events import setup_event_routes

        app = web.Application()
        setup_event_routes(app)
        for route in app.router.routes():
            method = getattr(route, "method", "").upper()
            path = getattr(getattr(route, "resource", None), "canonical", "") or ""
            if method == "POST" and path == "/api/events":
                self.fail("Public POST /api/events create route must not exist")
            if method == "POST" and "alarm-signal" in path:
                self.fail(f"Public alarm ingest route must not exist: POST {path}")
