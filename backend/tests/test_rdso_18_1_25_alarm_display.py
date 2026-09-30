"""RDSO 18.1.25 — alarm display reset independent of acknowledge."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from app.services.event_service import display_reset_event

CAMERA_ID = "507f1f77bcf86cd799439011"
EVENT_ID = "507f1f77bcf86cd799439013"
USER = {
    "_id": "o2",
    "role": "Operator",
    "permissions": ["Events"],
    "cameraAccess": {"allowedCameraUids": ["ip_192_168_41_106"]},
}


class DisplayResetService(unittest.IsolatedAsyncioTestCase):
    async def test_sets_display_reset_without_ack(self):
        oid = ObjectId(EVENT_ID)
        before = {
            "_id": oid,
            "camera_id": CAMERA_ID,
            "camera_uid": "ip_192_168_41_106",
            "source_type": "motion",
            "severity": "warning",
            "title": "Motion",
            "message": "motion",
            "occurred_at": "2026-09-08T10:00:00+00:00",
            "status": "open",
            "acknowledged": False,
            "actions_triggered": ["ui_notification"],
            "ui_notification": True,
            "metadata": {},
        }
        after = {
            **before,
            "metadata": {
                "display_reset": True,
                "display_reset_at": "2026-09-08T10:05:00+00:00",
                "display_reset_by": "o2",
            },
        }
        cam = {"_id": ObjectId(CAMERA_ID), "camera_uid": "ip_192_168_41_106"}
        find = AsyncMock(side_effect=[before, after])
        update = AsyncMock()
        with patch("app.services.event_service.events_collection") as col, patch(
            "app.services.event_service.get_camera_by_ref",
            new=AsyncMock(return_value=cam),
        ), patch(
            "app.services.event_service.user_can_access_camera",
            return_value=True,
        ):
            col.find_one = find
            col.update_one = update
            public = await display_reset_event(EVENT_ID, USER)

        self.assertIsNotNone(public)
        self.assertFalse(public["acknowledged"])
        self.assertEqual(public["status"], "open")
        self.assertTrue(public["metadata"].get("display_reset"))
        set_fields = update.await_args.args[1]["$set"]
        self.assertTrue(set_fields["metadata.display_reset"])
        self.assertNotIn("acknowledged", set_fields)

    async def test_acl_denies_reset(self):
        oid = ObjectId(EVENT_ID)
        doc = {
            "_id": oid,
            "camera_id": CAMERA_ID,
            "acknowledged": False,
            "status": "open",
            "metadata": {},
        }
        with patch("app.services.event_service.events_collection") as col, patch(
            "app.services.event_service.get_camera_by_ref",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "app.services.event_service.user_can_access_camera",
            return_value=False,
        ):
            col.find_one = AsyncMock(return_value=doc)
            out = await display_reset_event(EVENT_ID, USER)
        self.assertIsNone(out)
