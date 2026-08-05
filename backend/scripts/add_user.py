import asyncio
import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file (project root)
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(env_path)

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_DETAILS = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'nvr_database'
client = AsyncIOMotorClient(MONGO_DETAILS)
database = client[DATABASE_NAME]
user_collection = database.get_collection("users")

async def add_user(name, password, role="admin", email="", permissions=None):
    name = (name or "").strip()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    new_user_doc = {
        "name": name,
        "username": name,
        "role": role,
        "password": hashed_password,
        "email": email,
        "permissions": permissions or [],
        "cameraAccess": {
            "all": True,
            "allowedCameraGroups": [],
            "allowedCameraUids": [],
        },
        "status": "Active",
        "lastLogin": "Never",
    }
    result = await user_collection.insert_one(new_user_doc)
    print(f"User {name} added with ID: {result.inserted_id}")

# Run this to add a default user
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python add_user.py <username> <password>")
        sys.exit(1)
    username = sys.argv[1]
    password = sys.argv[2]
    asyncio.run(add_user(username, password))
