"""Reset admin login and remove duplicate user records without passwords."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bcrypt
from app.core.database import user_collection


async def main() -> None:
    password = (sys.argv[1] if len(sys.argv) > 1 else "admin").strip()
    if not password:
        raise SystemExit("Password cannot be empty")

    removed = 0
    async for user in user_collection.find({"$or": [{"name": "admin"}, {"username": "admin"}]}):
        if not user.get("password"):
            await user_collection.delete_one({"_id": user["_id"]})
            print(f"Removed duplicate admin without password: {user['_id']}")
            removed += 1

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    keeper = await user_collection.find_one(
        {"$or": [{"name": "admin"}, {"username": "admin"}]},
        sort=[("_id", 1)],
    )
    if not keeper:
        doc = {
            "name": "admin",
            "username": "admin",
            "role": "admin",
            "password": hashed,
            "status": "Active",
            "lastLogin": "Never",
            "permissions": [],
            "cameraAccess": {"allowedCameraGroups": [], "allowedCameraUids": []},
        }
        result = await user_collection.insert_one(doc)
        print(f"Created admin user: {result.inserted_id}")
        return

    await user_collection.update_one(
        {"_id": keeper["_id"]},
        {
            "$set": {
                "name": "admin",
                "username": "admin",
                "role": "admin",
                "password": hashed,
                "status": "Active",
            }
        },
    )
    print(f"Reset admin password on user {keeper['_id']} (removed {removed} duplicate(s))")
    print("Login with username: admin")


if __name__ == "__main__":
    asyncio.run(main())
