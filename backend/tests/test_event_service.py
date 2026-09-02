import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.alarm_rule_service import AlarmRuleValidationError, validate_rule_payload
from app.services.event_service import build_event_access_filter, create_event, list_events, sanitize_event_metadata


class TestAlarmRuleValidation(unittest.TestCase):
    def test_invalid_source_type(self):
        with self.assertRaises(AlarmRuleValidationError):
            validate_rule_payload(
                {
                    "name": "x",
                    "enabled": True,
                    "camera_id": "507f1f77bcf86cd799439011",
                    "trigger": {"source_type": "face_recognition"},
                    "actions": ["create_event"],
                    "severity": "info",
                    "cooldown_seconds": 10,
                }
            )

    def test_invalid_action(self):
        with self.assertRaises(AlarmRuleValidationError):
            validate_rule_payload(
                {
                    "name": "x",
                    "enabled": True,
                    "camera_id": "507f1f77bcf86cd799439011",
                    "trigger": {"source_type": "motion"},
                    "actions": ["email"],
                    "severity": "info",
                    "cooldown_seconds": 10,
                }
            )


class TestEventServiceInternal(unittest.IsolatedAsyncioTestCase):
    async def test_build_event_access_filter_admin_empty(self):
        admin = {"_id": "a1", "role": "Admin"}
        self.assertEqual(await build_event_access_filter(admin), {})

    async def test_build_event_access_filter_uid_only(self):
        user = {
            "_id": "o1",
            "role": "Operator",
            "cameraAccess": {"allowedCameraUids": ["ip_cam_a"], "allowedCameraGroups": []},
        }
        filt = await build_event_access_filter(user)
        self.assertEqual(filt, {"camera_uid": {"$in": ["ip_cam_a"]}})

    def test_sanitize_metadata_redacts_secrets(self):
        meta = sanitize_event_metadata({"password": "secret", "note": "ok"})
        self.assertEqual(meta["password"], "[REDACTED]")
        self.assertEqual(meta["note"], "ok")

    async def test_create_event_internal(self):
        cam_oid = ObjectId()
        with patch(
            "app.services.event_service.get_camera_by_ref",
            new_callable=AsyncMock,
            return_value={"_id": cam_oid, "camera_uid": "ip_test", "ip_address": "10.0.0.1"},
        ), patch(
            "app.services.event_service.events_collection.insert_one",
            new_callable=AsyncMock,
        ) as insert, patch(
            "app.services.event_service.events_collection.find_one",
            new_callable=AsyncMock,
            return_value={
                "_id": ObjectId(),
                "camera_id": str(cam_oid),
                "camera_uid": "ip_test",
                "rule_id": None,
                "source_type": "manual_test",
                "severity": "info",
                "title": "Test",
                "message": "Hello",
                "occurred_at": "2026-09-01T10:00:00+00:00",
                "status": "open",
                "acknowledged": False,
                "acknowledged_by": None,
                "acknowledged_at": None,
                "metadata": {},
            },
        ):
            insert.return_value = MagicMock(inserted_id=ObjectId())
            event = await create_event(
                camera_id=str(cam_oid),
                source_type="manual_test",
                severity="info",
                title="Test",
                message="Hello",
            )
        self.assertEqual(event["source_type"], "manual_test")
        self.assertFalse(event["acknowledged"])

    async def test_list_events_filters_ui_notification(self):
        admin = {"_id": "a1", "role": "Admin"}
        docs = [
            {
                "_id": ObjectId(),
                "camera_id": "c1",
                "camera_uid": "ip_1",
                "source_type": "signal_loss",
                "severity": "warning",
                "title": "Alert",
                "message": "lost",
                "occurred_at": "2026-09-01T10:00:00+00:00",
                "status": "open",
                "acknowledged": False,
                "actions_triggered": ["ui_notification"],
                "ui_notification": True,
                "metadata": {},
            }
        ]

        class _Cursor:
            def __init__(self, items):
                self._items = items
                self._i = 0

            def sort(self, *args, **kwargs):
                return self

            def skip(self, n):
                return self

            def limit(self, n):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._i >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._i]
                self._i += 1
                return item

        with patch(
            "app.services.event_service.events_collection.count_documents",
            new_callable=AsyncMock,
            return_value=1,
        ), patch(
            "app.services.event_service.events_collection.find",
            return_value=_Cursor(docs),
        ) as find_mock:
            result = await list_events(admin, ui_notification=True)

        self.assertEqual(result["total"], 1)
        filt = find_mock.call_args.args[0]
        self.assertEqual(filt.get("ui_notification"), True)
        self.assertTrue(result["items"][0]["ui_notification"])
