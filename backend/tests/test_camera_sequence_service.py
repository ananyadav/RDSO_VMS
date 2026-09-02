import unittest
from unittest.mock import AsyncMock, patch

from bson import ObjectId

from app.services.camera_sequence_service import (
    CameraSequenceValidationError,
    filter_authorized_camera_ids,
    sequence_to_public,
    validate_sequence_payload,
)

CAM_A = "507f1f77bcf86cd799439011"
CAM_B = "507f1f77bcf86cd799439012"
CAM_C = "507f1f77bcf86cd799439013"
CAM_D = "507f1f77bcf86cd799439014"


def _cam_doc(cid: str, *, group: str = "floor_a", uid: str | None = None) -> dict:
    return {
        "_id": ObjectId(cid),
        "camera_group": group,
        "camera_uid": uid or f"uid_{cid[-4:]}",
    }


class TestCameraSequenceValidation(unittest.TestCase):
    def test_valid_payload(self):
        payload = validate_sequence_payload(
            {
                "name": "Main Gate Patrol",
                "description": "Evening route",
                "enabled": True,
                "camera_ids": [CAM_A, CAM_B],
                "dwell_seconds": 10,
            }
        )
        self.assertEqual(payload["name"], "Main Gate Patrol")
        self.assertEqual(payload["camera_ids"], [CAM_A, CAM_B])
        self.assertEqual(payload["dwell_seconds"], 10)

    def test_order_preserved(self):
        payload = validate_sequence_payload(
            {
                "name": "Route",
                "enabled": True,
                "camera_ids": [CAM_D, CAM_A, CAM_C, CAM_B],
                "dwell_seconds": 15,
            }
        )
        self.assertEqual(payload["camera_ids"], [CAM_D, CAM_A, CAM_C, CAM_B])

    def test_name_trimmed(self):
        payload = validate_sequence_payload(
            {
                "name": "  Patrol  ",
                "enabled": True,
                "camera_ids": [CAM_A, CAM_B],
            }
        )
        self.assertEqual(payload["name"], "Patrol")

    def test_rejects_single_camera(self):
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {"name": "One", "enabled": True, "camera_ids": [CAM_A]}
            )

    def test_rejects_duplicate_camera_ids(self):
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {"name": "Dup", "enabled": True, "camera_ids": [CAM_A, CAM_A]}
            )

    def test_rejects_invalid_object_id(self):
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {"name": "Bad", "enabled": True, "camera_ids": ["not-an-id", CAM_A]}
            )

    def test_rejects_invalid_dwell(self):
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {
                    "name": "Bad dwell",
                    "enabled": True,
                    "camera_ids": [CAM_A, CAM_B],
                    "dwell_seconds": 1,
                }
            )
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {
                    "name": "Bad dwell",
                    "enabled": True,
                    "camera_ids": [CAM_A, CAM_B],
                    "dwell_seconds": 999,
                }
            )

    def test_rejects_forbidden_fields(self):
        with self.assertRaises(CameraSequenceValidationError):
            validate_sequence_payload(
                {
                    "name": "Secret",
                    "enabled": True,
                    "camera_ids": [CAM_A, CAM_B],
                    "password": "x",
                }
            )


class TestCameraSequenceAcl(unittest.TestCase):
    def test_operator_sees_only_accessible_cameras_in_order(self):
        operator = {
            "role": "Operator",
            "cameraAccess": {"allowedCameraGroups": ["floor_a"]},
        }
        cameras = {
            CAM_A: _cam_doc(CAM_A, group="floor_a"),
            CAM_B: _cam_doc(CAM_B, group="floor_b"),
            CAM_C: _cam_doc(CAM_C, group="floor_a"),
            CAM_D: _cam_doc(CAM_D, group="floor_c"),
        }
        filtered = filter_authorized_camera_ids(operator, [CAM_A, CAM_B, CAM_C, CAM_D], cameras)
        self.assertEqual(filtered, [CAM_A, CAM_C])

    def test_admin_sees_full_sequence(self):
        admin = {"role": "Admin"}
        cameras = {CAM_A: _cam_doc(CAM_A), CAM_B: _cam_doc(CAM_B)}
        filtered = filter_authorized_camera_ids(admin, [CAM_A, CAM_B], cameras)
        self.assertEqual(filtered, [CAM_A, CAM_B])

    def test_public_response_hides_unauthorized_ids(self):
        operator = {
            "role": "Operator",
            "cameraAccess": {"allowedCameraGroups": ["floor_a"]},
        }
        doc = {
            "_id": ObjectId("507f1f77bcf86cd799439099"),
            "name": "Patrol",
            "description": "",
            "enabled": True,
            "camera_ids": [CAM_A, CAM_B, CAM_C],
            "dwell_seconds": 10,
            "created_at": "2026-09-02T08:00:00+00:00",
            "updated_at": "2026-09-02T08:00:00+00:00",
        }
        cameras = {
            CAM_A: _cam_doc(CAM_A, group="floor_a"),
            CAM_B: _cam_doc(CAM_B, group="floor_b"),
            CAM_C: _cam_doc(CAM_C, group="floor_a"),
        }
        public = sequence_to_public(doc, user=operator, cameras_by_id=cameras)
        self.assertEqual(public["camera_ids"], [CAM_A, CAM_C])
        self.assertNotIn(CAM_B, public["camera_ids"])


class TestCameraSequenceServiceAsync(unittest.IsolatedAsyncioTestCase):
    async def test_create_validates_camera_existence(self):
        from app.services.camera_sequence_service import create_camera_sequence

        with patch(
            "app.services.camera_sequence_service.camera_collection.find",
        ) as find_mock:
            class _Cursor:
                def __init__(self, ids):
                    self._ids = ids

                def __aiter__(self):
                    self._iter = iter(self._ids)
                    return self

                async def __anext__(self):
                    try:
                        cid = next(self._iter)
                    except StopIteration:
                        raise StopAsyncIteration from None
                    return {"_id": ObjectId(cid)}

            find_mock.return_value = _Cursor([CAM_A, CAM_B])

            with patch(
                "app.services.camera_sequence_service.camera_sequences_collection.insert_one",
                new_callable=AsyncMock,
            ) as insert_mock, patch(
                "app.services.camera_sequence_service.camera_sequences_collection.find_one",
                new_callable=AsyncMock,
            ) as find_one_mock:
                oid = ObjectId("507f1f77bcf86cd799439099")
                insert_mock.return_value.inserted_id = oid
                find_one_mock.return_value = {
                    "_id": oid,
                    "name": "Patrol",
                    "description": "",
                    "enabled": True,
                    "camera_ids": [CAM_A, CAM_B],
                    "dwell_seconds": 10,
                    "created_by": "admin1",
                    "created_at": "2026-09-02T08:00:00+00:00",
                    "updated_at": "2026-09-02T08:00:00+00:00",
                }
                created = await create_camera_sequence(
                    {
                        "name": "Patrol",
                        "enabled": True,
                        "camera_ids": [CAM_A, CAM_B],
                        "dwell_seconds": 10,
                    },
                    created_by="admin1",
                )
        self.assertEqual(created["camera_ids"], [CAM_A, CAM_B])
        self.assertEqual(created["dwell_seconds"], 10)

    async def test_get_disabled_hidden_from_operator(self):
        from app.services.camera_sequence_service import get_camera_sequence

        with patch(
            "app.services.camera_sequence_service.get_sequence_doc",
            new_callable=AsyncMock,
            return_value={
                "_id": ObjectId("507f1f77bcf86cd799439099"),
                "name": "Disabled",
                "enabled": False,
                "camera_ids": [CAM_A, CAM_B],
                "dwell_seconds": 10,
            },
        ):
            result = await get_camera_sequence(
                "507f1f77bcf86cd799439099",
                {"role": "Operator", "permissions": ["Live View"]},
            )
        self.assertIsNone(result)

    async def test_list_excludes_zero_access_for_operator(self):
        from app.services.camera_sequence_service import list_camera_sequences

        doc = {
            "_id": ObjectId("507f1f77bcf86cd799439099"),
            "name": "No access",
            "enabled": True,
            "camera_ids": [CAM_B, CAM_C],
            "dwell_seconds": 10,
            "created_at": "2026-09-02T08:00:00+00:00",
            "updated_at": "2026-09-02T08:00:00+00:00",
        }

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *_args, **_kwargs):
                return self

            def __aiter__(self):
                self._iter = iter(self._items)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration from None

        operator = {
            "role": "Operator",
            "cameraAccess": {"allowedCameraGroups": ["floor_a"]},
        }

        with patch(
            "app.services.camera_sequence_service.camera_sequences_collection.find",
            return_value=_Cursor([doc]),
        ), patch(
            "app.services.camera_sequence_service._load_cameras_by_id",
            new_callable=AsyncMock,
            return_value={
                CAM_B: _cam_doc(CAM_B, group="floor_b"),
                CAM_C: _cam_doc(CAM_C, group="floor_c"),
            },
        ):
            data = await list_camera_sequences(operator)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)

    async def test_create_rejects_missing_camera(self):
        from app.services.camera_sequence_service import create_camera_sequence

        with patch(
            "app.services.camera_sequence_service.camera_collection.find",
        ) as find_mock:
            class _Cursor:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            find_mock.return_value = _Cursor()
            with self.assertRaises(CameraSequenceValidationError):
                await create_camera_sequence(
                    {
                        "name": "Patrol",
                        "enabled": True,
                        "camera_ids": [CAM_A, CAM_B],
                    },
                    created_by="admin1",
                )


if __name__ == "__main__":
    unittest.main()
