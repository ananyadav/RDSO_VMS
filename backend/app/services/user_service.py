import bcrypt
from datetime import datetime, timezone

from app.core.database import get_users, add_user as db_add_user, update_user as db_update_user, delete_user as db_delete_user, get_user_by_name, user_helper, user_collection


def _password_bytes(stored) -> bytes | None:
    if stored is None:
        return None
    if isinstance(stored, bytes):
        return stored
    if isinstance(stored, str):
        return stored.encode("utf-8")
    return None


def _verify_password(plain: str, stored) -> bool:
    pwd = _password_bytes(stored)
    if not pwd:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), pwd)
    except ValueError:
        return False


async def handle_login(data):
    try:
        username = (data.get("name") or data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        if not username or not password:
            return {"error": "Username and password required"}, 400
        user_in_db = await get_user_by_name(username)
        if not user_in_db:
            print(f"Failed login attempt for user: {username} (not found)")
            return {"error": "Invalid credentials"}, 401
        if (user_in_db.get("status") or "Active").strip().lower() == "disabled":
            return {"error": "Account is disabled"}, 403
        if not _verify_password(password, user_in_db.get("password")):
            print(f"Failed login attempt for user: {username}")
            return {"error": "Invalid credentials"}, 401

        await user_collection.update_one(
            {"_id": user_in_db["_id"]},
            {"$set": {"lastLogin": datetime.now(timezone.utc).isoformat()}},
        )
        print(f"Successful login for user: {username}")
        return user_helper(user_in_db), 200
    except Exception as e:
        print(f"Login error: {e}")
        return {"error": "Internal server error"}, 500

async def handle_add_user(user_data):
    try:
        new_user = await db_add_user(user_data)
        return new_user, 201
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        print(f"Error adding user: {e}")
        return {"error": "Failed to create user"}, 400

async def handle_update_user(user_id, user_data):
    try:
        updated_user = await db_update_user(user_id, user_data)
        if updated_user:
            return updated_user, 200
        return {"error": "User not found"}, 404
    except Exception as e:
        print(f"Error updating user: {e}")
        return {"error": "Invalid data"}, 400

async def handle_delete_user(user_id):
    if await db_delete_user(user_id):
        return "", 204
    return {"error": "User not found"}, 404
