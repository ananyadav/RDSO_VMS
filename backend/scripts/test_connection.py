#!/usr/bin/env python3
"""
Simple connection test using the app's database module.
Usage: python scripts/test_connection.py
"""

import asyncio
import sys
import os

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from app.core.database import get_users, get_all_cameras_from_db

async def test_database_operations():
    """Test database operations using the app's database module."""
    print("=" * 60)
    print("Testing Database Connection via App Module")
    print("=" * 60)
    print()
    
    try:
        # Test 1: Get users
        print("Test 1: Fetching users from database...")
        users = await get_users()
        print(f"✓ Successfully retrieved {len(users)} user(s)")
        if users:
            print("  Users:")
            for user in users:
                print(f"    - {user.get('name')} ({user.get('role')})")
        else:
            print("  No users found in database")
        print()
        
        # Test 2: Get cameras
        print("Test 2: Fetching cameras from database...")
        cameras = await get_all_cameras_from_db()
        print(f"✓ Successfully retrieved {len(cameras)} camera(s)")
        if cameras:
            print("  Cameras:")
            for camera in cameras:
                print(f"    - {camera.get('name')} ({camera.get('ip_address')})")
        else:
            print("  No cameras found in database")
        print()
        
        print("=" * 60)
        print("✅ Database connection is working correctly!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ DATABASE TEST FAILED")
        print("=" * 60)
        print(f"Error: {str(e)}")
        print()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_database_operations())
    sys.exit(0 if success else 1)

