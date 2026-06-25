#!/usr/bin/env python3
"""
Fix camera password in the database - converts null passwords to empty string or sets a new password.
Usage: 
  python fix_camera_password.py <camera_id> [password]
  If password is not provided, sets it to empty string (for cameras without password)
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

from app.services.rtsp_utils import sync_camera_rtsp_urls
from app.services.camera_sync import schedule_camera_side_effects

MONGO_DETAILS = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
DATABASE_NAME = 'nvr_database'
client = AsyncIOMotorClient(MONGO_DETAILS)
database = client[DATABASE_NAME]
camera_collection = database.get_collection("cameras")

async def fix_camera_password(camera_id, new_password=None):
    """Update camera password - converts null to empty string or sets new password."""
    try:
        # Find the camera first
        camera = await camera_collection.find_one({"_id": ObjectId(camera_id)})
        if not camera:
            print(f"❌ No camera found with ID: {camera_id}")
            return False
        
        print(f"📹 Found camera: {camera.get('name', 'Unknown')} ({camera.get('ip_address', 'N/A')})")
        print(f"   Current password: {'null' if camera.get('password') is None else ('***' if camera.get('password') else '(empty)')}")
        
        # Determine what password to set
        if new_password is None:
            # If password is null, set to empty string
            password_to_set = "" if camera.get('password') is None else camera.get('password')
            action = "converted null to empty string" if camera.get('password') is None else "kept existing"
        else:
            password_to_set = new_password
            action = "updated"
        
        # Update the camera
        synced = sync_camera_rtsp_urls({**camera, "password": password_to_set})
        patch = {"password": password_to_set}
        for key in (
            "main_rtsp_url",
            "sub_rtsp_url",
            "preview_rtsp_url",
            "rtsp_url",
            "rtsp_url_source",
        ):
            if synced.get(key) is not None:
                patch[key] = synced[key]

        result = await camera_collection.update_one(
            {"_id": ObjectId(camera_id)},
            {"$set": patch},
        )
        
        if result.modified_count > 0:
            print(f"✅ Camera password {action} successfully!")
            print(f"   New password: {'(empty)' if not password_to_set else '***'}")
            schedule_camera_side_effects(
                camera_id,
                existing=camera,
                updated_fields=patch,
                reason="password_fix",
            )
            return True
        else:
            print(f"⚠️  No changes made (password may already be set correctly)")
            return True
    except Exception as e:
        print(f"❌ Error updating camera: {e}")
        return False

async def fix_all_null_passwords():
    """Fix all cameras with null passwords in the database."""
    try:
        # Find all cameras with null passwords
        cameras = await camera_collection.find({"password": None}).to_list(length=100)
        
        if not cameras:
            print("✅ No cameras with null passwords found!")
            return
        
        print(f"🔍 Found {len(cameras)} camera(s) with null passwords:")
        for cam in cameras:
            print(f"   - {cam.get('name', 'Unknown')} ({cam.get('ip_address', 'N/A')}) - ID: {cam['_id']}")
        
        # Update all of them
        result = await camera_collection.update_many(
            {"password": None},
            {"$set": {"password": ""}}
        )
        
        print(f"\n✅ Updated {result.modified_count} camera(s) - converted null passwords to empty strings")
    except Exception as e:
        print(f"❌ Error fixing cameras: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Fix specific camera: python fix_camera_password.py <camera_id> [password]")
        print("  Fix all null passwords: python fix_camera_password.py --fix-all")
        print("\nExample:")
        print("  python fix_camera_password.py 6969f5c7d0d84e736158409e")
        print("  python fix_camera_password.py 6969f5c7d0d84e736158409e mypassword123")
        sys.exit(1)
    
    if sys.argv[1] == "--fix-all":
        asyncio.run(fix_all_null_passwords())
    else:
        camera_id = sys.argv[1]
        new_password = sys.argv[2] if len(sys.argv) > 2 else None
        asyncio.run(fix_camera_password(camera_id, new_password))
