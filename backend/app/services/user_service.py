import bcrypt
from app.core.database import get_users, add_user as db_add_user, update_user as db_update_user, delete_user as db_delete_user, get_user_by_name, user_helper

async def handle_login(data):
    try:
        # Accept both "name" and "username" for flexibility
        username = data.get("name") or data.get("username")
        password = data.get("password")
        if not username or not password:
            return {"error": "Username and password required"}, 400
        user_in_db = await get_user_by_name(username)
        if user_in_db and "password" in user_in_db and bcrypt.checkpw(password.encode('utf-8'), user_in_db["password"]):
            print(f"Successful login for user: {username}")
            return user_helper(user_in_db), 200
        print(f"Failed login attempt for user: {username}")
        return {"error": "Invalid credentials"}, 401
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
