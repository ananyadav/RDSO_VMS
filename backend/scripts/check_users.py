import asyncio
import sys
import os

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from app.core.database import get_users

async def check_users():
    users = await get_users()
    print("Users in database:")
    for user in users:
        print(f"- {user['name']} ({user['role']})")

if __name__ == "__main__":
    asyncio.run(check_users())
