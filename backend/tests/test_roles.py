import unittest

from app.core.roles import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_SUPER_ADMIN,
    ROLE_VIEWER,
    is_ops_admin,
    is_operator,
    is_super_admin,
    normalize_role,
)
from app.services.camera_access import is_admin


class TestRoles(unittest.TestCase):
    def test_normalize_existing_labels(self):
        self.assertEqual(normalize_role("Admin"), ROLE_ADMIN)
        self.assertEqual(normalize_role("admin"), ROLE_ADMIN)
        self.assertEqual(normalize_role("Operator"), ROLE_OPERATOR)
        self.assertEqual(normalize_role("Viewer"), ROLE_VIEWER)
        self.assertEqual(normalize_role("SUPER_ADMIN"), ROLE_SUPER_ADMIN)
        self.assertEqual(normalize_role("super_admin"), ROLE_SUPER_ADMIN)

    def test_is_admin_includes_super_admin(self):
        self.assertTrue(is_admin({"role": "Admin"}))
        self.assertTrue(is_admin({"role": "SUPER_ADMIN"}))
        self.assertTrue(is_ops_admin({"role": "SUPER_ADMIN"}))
        self.assertFalse(is_admin({"role": "Operator"}))
        self.assertFalse(is_admin({"role": "Viewer"}))

    def test_super_admin_helpers(self):
        self.assertTrue(is_super_admin({"role": "SUPER_ADMIN"}))
        self.assertFalse(is_super_admin({"role": "Admin"}))
        self.assertTrue(is_operator({"role": "Operator"}))
        self.assertFalse(is_operator({"role": "Admin"}))


if __name__ == "__main__":
    unittest.main()
