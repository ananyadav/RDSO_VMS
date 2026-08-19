"""Promote an existing user to SUPER_ADMIN.

This is a trusted-engineer CLI. It is not exposed over HTTP or the frontend.

Usage:
  python scripts/promote_super_admin.py --username <existing-user>
  python scripts/promote_super_admin.py --user-id <mongo-object-id>
  python scripts/promote_super_admin.py --username <existing-user> --confirm

The first invocation prints the intended target and exits.
Pass --confirm to apply the role change.

Does not create accounts, does not set a password, and does not promote anyone
automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bson.objectid import ObjectId
from bson.errors import InvalidId

from app.core.database import user_collection
from app.core.roles import ROLE_SUPER_ADMIN, stored_role_label
from app.services.audit_service import write_audit

ALL_PERMISSIONS = [
    "Live View",
    "recording.view",
    "Events",
    "Cameras",
    "System",
    "Users",
]


async def _find_users(username: str | None, user_id: str | None) -> list[dict]:
    if user_id:
        try:
            oid = ObjectId(user_id)
        except (InvalidId, TypeError, ValueError):
            raise SystemExit(f"Invalid user id: {user_id}")
        doc = await user_collection.find_one({"_id": oid})
        return [doc] if doc else []
    key = (username or "").strip()
    if not key:
        raise SystemExit("Provide --username or --user-id")
    pattern = {"$regex": f"^{key}$", "$options": "i"}
    cursor = user_collection.find({"$or": [{"name": pattern}, {"username": pattern}]})
    return [doc async for doc in cursor]


def _print_target(user: dict) -> None:
    print("Intended SUPER_ADMIN target:")
    print(f"  id:       {user.get('_id')}")
    print(f"  name:     {user.get('name') or ''}")
    print(f"  username: {user.get('username') or user.get('name') or ''}")
    print(f"  role:     {stored_role_label(user) or user.get('role') or ''}")
    print(f"  status:   {user.get('status') or ''}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Promote an existing user to SUPER_ADMIN")
    parser.add_argument("--username", help="Existing login name (exact, case-insensitive)")
    parser.add_argument("--user-id", help="Existing MongoDB user id")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to apply the role change after reviewing the target",
    )
    args = parser.parse_args()
    if not args.username and not args.user_id:
        parser.error("Provide --username or --user-id")

    matches = await _find_users(args.username, args.user_id)
    if not matches:
        raise SystemExit("No matching user found. SUPER_ADMIN was not created.")
    if len(matches) != 1:
        print("Multiple users matched; refusing to guess.")
        for user in matches:
            print(f"  {user.get('_id')}  {user.get('name')}  {user.get('role')}")
        raise SystemExit(1)

    target = matches[0]
    _print_target(target)

    current = stored_role_label(target)
    if current == ROLE_SUPER_ADMIN:
        print("User is already SUPER_ADMIN. No change applied.")
        return

    if not args.confirm:
        print()
        print("No change applied. Re-run with --confirm to promote this user.")
        return

    await user_collection.update_one(
        {"_id": target["_id"]},
        {
            "$set": {
                "role": ROLE_SUPER_ADMIN,
                "permissions": list(ALL_PERMISSIONS),
                "cameraAccess": {
                    "all": True,
                    "allowedCameraGroups": [],
                    "allowedCameraUids": [],
                },
            }
        },
    )
    await write_audit(
        action="USER_ROLE_CHANGED",
        actor={
            "_id": "bootstrap",
            "name": "promote_super_admin.py",
            "username": "promote_super_admin.py",
            "role": "BOOTSTRAP",
        },
        resource_type="user",
        resource_id=str(target["_id"]),
        resource_label=(target.get("username") or target.get("name") or ""),
        success=True,
        changes={"role": {"before": target.get("role"), "after": ROLE_SUPER_ADMIN}},
        metadata={"source": "promote_super_admin.py"},
    )
    print(f"Promoted {target.get('name')} ({target.get('_id')}) to SUPER_ADMIN.")


if __name__ == "__main__":
    asyncio.run(main())
