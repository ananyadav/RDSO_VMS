import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import make_mocked_request

from app.core.access_control import PERMISSION_LIVE_VIEW
from app.routes.camera_sequences import (
    create_camera_sequence_endpoint,
    delete_camera_sequence_endpoint,
    get_camera_sequence_endpoint,
    list_camera_sequences_endpoint,
    update_camera_sequence_endpoint,
)
from app.services.audit_service import (
    ACTION_CAMERA_SEQUENCE_CREATED,
    ACTION_CAMERA_SEQUENCE_DELETED,
    ACTION_CAMERA_SEQUENCE_UPDATED,
)
from app.services.camera_sequence_service import CameraSequenceValidationError

CAM_A = "507f1f77bcf86cd799439011"
CAM_B = "507f1f77bcf86cd799439012"
CAM_C = "507f1f77bcf86cd799439013"
SEQ_ID = "507f1f77bcf86cd799439099"

VALID_SEQUENCE = {
    "id": SEQ_ID,
    "name": "Main Gate Patrol",
    "description": "",
    "enabled": True,
    "camera_ids": [CAM_A, CAM_B, CAM_C],
    "dwell_seconds": 10,
    "created_at": "2026-09-02T08:00:00+00:00",
    "updated_at": "2026-09-02T08:00:00+00:00",
}

FILTERED_SEQUENCE = {
    **VALID_SEQUENCE,
    "camera_ids": [CAM_A, CAM_C],
}


def _request(method: str, path: str, user=None, match_info=None):
    request = make_mocked_request(method, path, match_info=match_info or {})
    request["auth_user"] = user
    return request


ADMIN = {"_id": "a1", "name": "ops", "role": "Admin", "permissions": []}
SUPER = {"_id": "s1", "name": "root", "role": "SUPER_ADMIN", "permissions": []}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator", "permissions": [PERMISSION_LIVE_VIEW]}
VIEWER = {"_id": "v1", "name": "watch", "role": "Viewer", "permissions": []}
RESTRICTED_OP = {
    **OPERATOR,
    "cameraAccess": {"allowedCameraGroups": ["floor_a"]},
}


class TestCameraSequenceRoutes(unittest.IsolatedAsyncioTestCase):
    async def test_admin_creates_valid_sequence(self):
        with patch("app.routes.camera_sequences.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.camera_sequences.create_camera_sequence",
            new_callable=AsyncMock,
            return_value=VALID_SEQUENCE,
        ), patch(
            "app.routes.camera_sequences.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit_mock:
            read_body.return_value = (
                {
                    "name": "Main Gate Patrol",
                    "enabled": True,
                    "camera_ids": [CAM_A, CAM_B, CAM_C],
                    "dwell_seconds": 10,
                },
                None,
            )
            response = await create_camera_sequence_endpoint(
                _request("POST", "/api/camera-sequences", ADMIN)
            )
        self.assertEqual(response.status, 201)
        body = json.loads(response.text)
        self.assertEqual(body["camera_ids"], [CAM_A, CAM_B, CAM_C])
        self.assertEqual(audit_mock.await_args.kwargs["action"], ACTION_CAMERA_SEQUENCE_CREATED)

    async def test_super_admin_can_create(self):
        with patch("app.routes.camera_sequences.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.camera_sequences.create_camera_sequence",
            new_callable=AsyncMock,
            return_value=VALID_SEQUENCE,
        ), patch(
            "app.routes.camera_sequences.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            read_body.return_value = (
                {"name": "Patrol", "enabled": True, "camera_ids": [CAM_A, CAM_B]},
                None,
            )
            response = await create_camera_sequence_endpoint(
                _request("POST", "/api/camera-sequences", SUPER)
            )
        self.assertEqual(response.status, 201)

    async def test_operator_cannot_create(self):
        response = await create_camera_sequence_endpoint(
            _request("POST", "/api/camera-sequences", OPERATOR)
        )
        self.assertEqual(response.status, 403)

    async def test_viewer_cannot_create(self):
        response = await create_camera_sequence_endpoint(
            _request("POST", "/api/camera-sequences", VIEWER)
        )
        self.assertEqual(response.status, 403)

    async def test_validation_error_on_create(self):
        with patch("app.routes.camera_sequences.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.camera_sequences.create_camera_sequence",
            new_callable=AsyncMock,
            side_effect=CameraSequenceValidationError("camera_ids must contain at least 2 cameras"),
        ):
            read_body.return_value = (
                {"name": "Bad", "enabled": True, "camera_ids": [CAM_A]},
                None,
            )
            response = await create_camera_sequence_endpoint(
                _request("POST", "/api/camera-sequences", ADMIN)
            )
        self.assertEqual(response.status, 400)

    async def test_operator_can_list_filtered_sequences(self):
        with patch(
            "app.routes.camera_sequences.list_camera_sequences",
            new_callable=AsyncMock,
            return_value={"items": [FILTERED_SEQUENCE], "total": 1, "limit": 100, "offset": 0},
        ):
            response = await list_camera_sequences_endpoint(
                _request("GET", "/api/camera-sequences", RESTRICTED_OP)
            )
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["items"][0]["camera_ids"], [CAM_A, CAM_C])

    async def test_operator_get_sequence_acl_filtered(self):
        with patch(
            "app.routes.camera_sequences.get_camera_sequence",
            new_callable=AsyncMock,
            return_value=FILTERED_SEQUENCE,
        ):
            response = await get_camera_sequence_endpoint(
                _request("GET", f"/api/camera-sequences/{SEQ_ID}", RESTRICTED_OP, {"id": SEQ_ID})
            )
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual(body["camera_ids"], [CAM_A, CAM_C])
        self.assertNotIn(CAM_B, body["camera_ids"])

    async def test_operator_get_zero_access_returns_404(self):
        with patch(
            "app.routes.camera_sequences.get_camera_sequence",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await get_camera_sequence_endpoint(
                _request("GET", f"/api/camera-sequences/{SEQ_ID}", RESTRICTED_OP, {"id": SEQ_ID})
            )
        self.assertEqual(response.status, 404)

    async def test_unauthenticated_list_401(self):
        response = await list_camera_sequences_endpoint(_request("GET", "/api/camera-sequences", None))
        self.assertEqual(response.status, 401)

    async def test_admin_update_audited(self):
        before_doc = {
            "_id": SEQ_ID,
            "name": "Old",
            "enabled": True,
            "camera_ids": [CAM_A, CAM_B],
            "dwell_seconds": 10,
        }
        updated = {**VALID_SEQUENCE, "name": "Renamed", "dwell_seconds": 20}
        with patch("app.routes.camera_sequences.read_json_body", new_callable=AsyncMock) as read_body, patch(
            "app.routes.camera_sequences.get_sequence_doc",
            new_callable=AsyncMock,
            return_value=before_doc,
        ), patch(
            "app.routes.camera_sequences.update_camera_sequence",
            new_callable=AsyncMock,
            return_value=updated,
        ), patch(
            "app.routes.camera_sequences.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit_mock:
            read_body.return_value = ({"name": "Renamed", "dwell_seconds": 20}, None)
            response = await update_camera_sequence_endpoint(
                _request("PUT", f"/api/camera-sequences/{SEQ_ID}", ADMIN, {"id": SEQ_ID})
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(audit_mock.await_args.kwargs["action"], ACTION_CAMERA_SEQUENCE_UPDATED)

    async def test_operator_cannot_update(self):
        response = await update_camera_sequence_endpoint(
            _request("PUT", f"/api/camera-sequences/{SEQ_ID}", OPERATOR, {"id": SEQ_ID})
        )
        self.assertEqual(response.status, 403)

    async def test_admin_delete_audited(self):
        before_doc = {
            "_id": SEQ_ID,
            "name": "Patrol",
            "enabled": False,
            "camera_ids": [CAM_A, CAM_B],
            "dwell_seconds": 10,
        }
        with patch(
            "app.routes.camera_sequences.get_sequence_doc",
            new_callable=AsyncMock,
            return_value=before_doc,
        ), patch(
            "app.routes.camera_sequences.delete_camera_sequence",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.routes.camera_sequences.commit_critical_audit",
            new_callable=AsyncMock,
            return_value=True,
        ) as audit_mock:
            response = await delete_camera_sequence_endpoint(
                _request("DELETE", f"/api/camera-sequences/{SEQ_ID}", ADMIN, {"id": SEQ_ID})
            )
        self.assertEqual(response.status, 204)
        self.assertEqual(audit_mock.await_args.kwargs["action"], ACTION_CAMERA_SEQUENCE_DELETED)

    async def test_operator_cannot_delete(self):
        response = await delete_camera_sequence_endpoint(
            _request("DELETE", f"/api/camera-sequences/{SEQ_ID}", OPERATOR, {"id": SEQ_ID})
        )
        self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
