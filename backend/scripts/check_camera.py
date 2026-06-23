#!/usr/bin/env python3
"""Quick script to check camera details"""

import sys
import os
from dotenv import load_dotenv

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

async def check_camera(camera_id):
    cam = await camera_collection.find_one({"_id": ObjectId(camera_id)})
    if cam:
        print(f"Name: {cam.get('name')}")
        print(f"IP: {cam.get('ip_address')}")
        print(f"Port: {cam.get('port')}")
        print(f"Username: {cam.get('username')}")
        print(f"Password: {repr(cam.get('password'))} (None={cam.get('password') is None}, Empty={cam.get('password') == ''})")
    else:
        print("Camera not found")

if __name__ == "__main__":
    camera_id = sys.argv[1] if len(sys.argv) > 1 else "6969f5c7d0d84e736158409e"
    asyncio.run(check_camera(camera_id))
