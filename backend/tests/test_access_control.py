import unittest

from app.core.access_control import (
    PERMISSION_PLAYBACK,
    deny_unless_playback_permission,
    has_permission,
)
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
        viewer_ok = {"role": "Viewer", "permissions": [PERMISSION_PLAYBACK]}
        viewer_no = {"role": "Viewer", "permissions": ["Live View"]}
        operator = {"role": "Operator", "permissions": ["Live View"]}
        self.assertTrue(has_permission(admin, PERMISSION_PLAYBACK))
        self.assertTrue(has_permission(super_admin, PERMISSION_PLAYBACK))
        self.assertTrue(has_permission(viewer_ok, PERMISSION_PLAYBACK))
        self.assertFalse(has_permission(viewer_no, PERMISSION_PLAYBACK))
        self.assertFalse(has_permission(operator, PERMISSION_PLAYBACK))


if __name__ == "__main__":
    unittest.main()
