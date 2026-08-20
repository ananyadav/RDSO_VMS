import unittest

from app.core.access_control import (
    PERMISSION_LIVE_VIEW,
    PERMISSION_PLAYBACK,
    PERMISSION_RECORDING_VIEW,
    has_permission,
    has_recording_view,
)
from app.core.database import user_helper
from app.services.camera_access import (
    normalize_camera_access,
    user_can_access_camera,
    user_can_access_stream,
)


class TestAccessControl(unittest.TestCase):
    def test_anonymous_camera_access_denied(self):
        self.assertFalse(user_can_access_camera(None, "ip_192_168_1_1"))
        self.assertFalse(user_can_access_stream(None, "ip_192_168_1_1_sub"))

    def test_viewer_group_access(self):
        user = {
            "role": "Viewer",
            "cameraAccess": {
                "allowedCameraGroups": ["site_building_floor"],
                "allowedCameraUids": [],
            },
        }
        cam = {"camera_group": "site_building_floor", "camera_uid": "ip_192_168_1_10"}
        self.assertTrue(user_can_access_camera(user, "ip_192_168_1_10", cam))
        self.assertTrue(user_can_access_stream(user, "ip_192_168_1_10_sub", cam))

    def test_viewer_denied_other_group(self):
        user = {
            "role": "Viewer",
            "cameraAccess": {
                "allowedCameraGroups": ["site_building_floor"],
                "allowedCameraUids": [],
            },
        }
        cam = {"camera_group": "other_floor", "camera_uid": "ip_192_168_1_11"}
        self.assertFalse(user_can_access_camera(user, "ip_192_168_1_11", cam))
        self.assertFalse(user_can_access_stream(user, "ip_192_168_1_11_sub", cam))

    def test_viewer_legacy_all_is_restricted(self):
        access = normalize_camera_access(
            {"role": "Viewer", "cameraAccess": {"accessType": "all"}}
        )
        self.assertFalse(access["all"])

    def test_playback_permission(self):
        admin = {"role": "Admin", "permissions": []}
        super_admin = {"role": "SUPER_ADMIN", "permissions": []}
        viewer_ok = {"role": "Viewer", "permissions": [PERMISSION_RECORDING_VIEW]}
        viewer_no = {"role": "Viewer", "permissions": ["Live View"]}
        operator = {"role": "Operator", "permissions": ["Live View"]}
        operator_view = {"role": "Operator", "permissions": [PERMISSION_RECORDING_VIEW]}
        legacy_playback = {"role": "Operator", "permissions": [PERMISSION_PLAYBACK]}
        self.assertTrue(has_permission(admin, PERMISSION_RECORDING_VIEW))
        self.assertTrue(has_permission(super_admin, PERMISSION_RECORDING_VIEW))
        self.assertTrue(has_recording_view(admin))
        self.assertTrue(has_recording_view(super_admin))
        self.assertTrue(has_recording_view(viewer_ok))
        self.assertFalse(has_recording_view(viewer_no))
        self.assertFalse(has_recording_view(operator))
        self.assertTrue(has_recording_view(operator_view))
        self.assertFalse(has_recording_view(legacy_playback))
        self.assertFalse(has_permission(operator, PERMISSION_PLAYBACK))

    def test_operator_and_viewer_have_live_view_by_role(self):
        self.assertTrue(has_permission({"role": "Operator", "permissions": []}, PERMISSION_LIVE_VIEW))
        self.assertTrue(has_permission({"role": "Viewer", "permissions": []}, PERMISSION_LIVE_VIEW))
        self.assertTrue(has_permission({"role": "SUPER_ADMIN", "permissions": []}, PERMISSION_LIVE_VIEW))
        self.assertTrue(has_permission({"role": "Admin", "permissions": []}, PERMISSION_LIVE_VIEW))
        self.assertFalse(has_permission({"role": "Operator", "permissions": []}, "System"))

    def test_super_admin_session_profile_is_unrestricted(self):
        payload = user_helper(
            {
                "_id": "s1",
                "name": "root",
                "role": "superadmin",
                "cameraAccess": {"allowedCameraGroups": ["only_this_floor"]},
            }
        )
        self.assertEqual(payload["role"], "SUPER_ADMIN")
        self.assertTrue(payload["cameraAccess"]["all"])
        cam = {"camera_group": "other_floor", "camera_uid": "ip_1"}
        self.assertTrue(user_can_access_stream({"role": "SUPER_ADMIN", "permissions": []}, "ip_1_sub", cam))


if __name__ == "__main__":
    unittest.main()
