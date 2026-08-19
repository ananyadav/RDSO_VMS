import unittest

from app.services.user_rbac import (
    GENERIC_FORBIDDEN,
    can_create_role,
    can_delete_user,
    can_list_user,
    can_modify_user,
    visible_users,
)

SUPER = {"_id": "sa1", "name": "root", "role": "SUPER_ADMIN"}
ADMIN = {"_id": "a1", "name": "ops", "role": "Admin"}
ADMIN2 = {"_id": "a2", "name": "ops2", "role": "Admin"}
OPERATOR = {"_id": "o1", "name": "camop", "role": "Operator"}
VIEWER = {"_id": "v1", "name": "view", "role": "Viewer"}


class TestUserRbac(unittest.TestCase):
    def test_admin_cannot_see_super_admin(self):
        users = visible_users(ADMIN, [SUPER, ADMIN2, OPERATOR, VIEWER])
        names = {u["name"] for u in users}
        self.assertNotIn("root", names)
        self.assertIn("ops2", names)
        self.assertIn("camop", names)
        self.assertFalse(can_list_user(ADMIN, SUPER))
        self.assertTrue(can_list_user(SUPER, SUPER))
        self.assertTrue(can_list_user(SUPER, ADMIN))

    def test_operator_cannot_manage_users(self):
        self.assertFalse(can_list_user(OPERATOR, OPERATOR))
        allowed, reason = can_create_role(OPERATOR, "Operator")
        self.assertFalse(allowed)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        allowed, reason = can_modify_user(OPERATOR, OPERATOR, {"status": "Disabled"})
        self.assertFalse(allowed)
        self.assertEqual(reason, GENERIC_FORBIDDEN)

    def test_admin_may_manage_operator_not_admin(self):
        ok, reason = can_create_role(ADMIN, "Operator")
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        ok, _ = can_modify_user(ADMIN, OPERATOR, {"status": "Disabled"})
        self.assertTrue(ok)
        ok, reason = can_create_role(ADMIN, "Admin")
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_create_role(ADMIN, "SUPER_ADMIN")
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_modify_user(ADMIN, ADMIN2, {"status": "Disabled"})
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_modify_user(ADMIN, SUPER, {"status": "Disabled"})
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_delete_user(ADMIN, ADMIN2)
        self.assertFalse(ok)
        ok, reason = can_delete_user(ADMIN, SUPER)
        self.assertFalse(ok)
        ok, _ = can_delete_user(ADMIN, OPERATOR)
        self.assertTrue(ok)

    def test_no_self_promotion(self):
        ok, reason = can_modify_user(ADMIN, ADMIN, {"role": "SUPER_ADMIN"})
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_modify_user(OPERATOR, OPERATOR, {"role": "Admin"})
        self.assertFalse(ok)
        ok, reason = can_modify_user(SUPER, SUPER, {"role": "Admin"})
        self.assertFalse(ok)

    def test_admin_cannot_assign_privileged_roles(self):
        ok, reason = can_modify_user(ADMIN, OPERATOR, {"role": "Admin"})
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        ok, reason = can_modify_user(ADMIN, OPERATOR, {"role": "SUPER_ADMIN"})
        self.assertFalse(ok)
        self.assertEqual(reason, GENERIC_FORBIDDEN)

    def test_super_admin_may_manage_admin(self):
        ok, _ = can_create_role(SUPER, "Admin")
        self.assertTrue(ok)
        ok, _ = can_create_role(SUPER, "SUPER_ADMIN")
        self.assertTrue(ok)
        ok, _ = can_modify_user(SUPER, ADMIN, {"status": "Disabled", "role": "Admin"})
        self.assertTrue(ok)
        ok, _ = can_delete_user(SUPER, ADMIN)
        self.assertTrue(ok)

    def test_forbidden_message_does_not_name_super_admin(self):
        _, reason = can_modify_user(ADMIN, SUPER, {"email": "x"})
        self.assertEqual(reason, GENERIC_FORBIDDEN)
        self.assertNotIn("SUPER_ADMIN", reason)

    def test_concealed_from_admin(self):
        from app.services.user_rbac import is_concealed_from

        self.assertTrue(is_concealed_from(ADMIN, SUPER))
        self.assertFalse(is_concealed_from(SUPER, SUPER))
        self.assertFalse(is_concealed_from(ADMIN, OPERATOR))


if __name__ == "__main__":
    unittest.main()
