#!/usr/bin/env python3
"""
Update camera details in the database.
Usage: python update_camera.py <camera_id> <name> <ip> <port> <username> <password> <model>
"""

import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file (project root)
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(env_path)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

MONGO_DETAILS = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'nvr_database'
client = AsyncIOMotorClient(MONGO_DETAILS)
database = client[DATABASE_NAME]
camera_collection = database.get_collection("cameras")

async def update_camera(camera_id, name, ip, port, username, password, model):
    try:
        result = await camera_collection.update_one(
            {"_id": ObjectId(camera_id)},
            {"$set": {
                "name": name,
                "ip_address": ip,
                "port": int(port),
                "username": username,
                "password": password,
                "model": model
            }}
        )
        if result.modified_count > 0:
            print(f"Camera {camera_id} updated successfully.")
        else:
            print(f"No camera found with ID {camera_id}.")
    except Exception as e:
        print(f"Error updating camera: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 8:
        print("Usage: python update_camera.py <camera_id> <name> <ip> <port> <username> <password> <model>")
        sys.exit(1)

    camera_id, name, ip, port, username, password, model = sys.argv[1:]
    asyncio.run(update_camera(camera_id, name, ip, port, username, password, model))
