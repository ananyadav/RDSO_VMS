#!/usr/bin/env python3
"""
Test script to verify MongoDB Atlas connection and database operations.
Usage: python test_db_connection.py
"""

import asyncio
import sys
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

# Load environment variables from .env file (project root)
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(env_path)

MONGO_URI = os.getenv('MONGODB_URI')
DATABASE_NAME = 'nvr_database'

async def test_connection():
    """Test MongoDB connection and basic operations."""
    print("=" * 60)
    print("MongoDB Atlas Connection Test")
    print("=" * 60)
    
    # Check if MONGODB_URI is loaded
    if not MONGO_URI:
        print("❌ ERROR: MONGODB_URI not found in .env file")
        print("   Please make sure .env file exists and contains MONGODB_URI")
        return False
    
    print(f"✓ MongoDB URI loaded from .env")
    print(f"  URI: {MONGO_URI[:50]}...")  # Show first 50 chars for security
    print()
    
    try:
        # Test 1: Create client and test connection
        print("Test 1: Connecting to MongoDB Atlas...")
        client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        
        # Ping the server
        await client.admin.command('ping')
        print("✓ Successfully connected to MongoDB Atlas!")
        print()
        
        # Test 2: List databases
        print("Test 2: Listing available databases...")
        db_list = await client.list_database_names()
        print(f"✓ Found {len(db_list)} database(s): {', '.join(db_list)}")
        print()
        
        # Test 3: Access target database
        print(f"Test 3: Accessing database '{DATABASE_NAME}'...")
        database = client[DATABASE_NAME]
        
        # List collections
        collections = await database.list_collection_names()
        print(f"✓ Database '{DATABASE_NAME}' accessed successfully")
        print(f"  Collections: {', '.join(collections) if collections else 'None (will be created on first use)'}")
        print()
        
        # Test 4: Test write operation (insert a test document)
        print("Test 4: Testing write operation...")
        test_collection = database.get_collection("connection_test")
        test_doc = {
            "test": True,
            "timestamp": datetime.utcnow(),
            "message": "Connection test successful"
        }
        result = await test_collection.insert_one(test_doc)
        print(f"✓ Write test successful! Inserted document ID: {result.inserted_id}")
        print()
        
        # Test 5: Test read operation
        print("Test 5: Testing read operation...")
        retrieved_doc = await test_collection.find_one({"_id": result.inserted_id})
        if retrieved_doc:
            print(f"✓ Read test successful! Retrieved document: {retrieved_doc.get('message')}")
        else:
            print("❌ Read test failed!")
        print()
        
        # Test 6: Test collections that should exist
        print("Test 6: Checking required collections...")
        required_collections = ['users', 'cameras']
        for coll_name in required_collections:
            collection = database.get_collection(coll_name)
            count = await collection.count_documents({})
            print(f"  - {coll_name}: {count} document(s)")
        print()
        
        # Cleanup: Remove test document
        print("Cleaning up test data...")
        await test_collection.delete_one({"_id": result.inserted_id})
        print("✓ Test document removed")
        print()
        
        # Close connection
        client.close()
        
        print("=" * 60)
        print("✅ ALL TESTS PASSED! MongoDB Atlas is working correctly.")
        print("=" * 60)
        return True
        
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ CONNECTION TEST FAILED")
        print("=" * 60)
        print(f"Error: {str(e)}")
        print()
        print("Troubleshooting tips:")
        print("1. Check if your MongoDB Atlas connection string is correct")
        print("2. Verify your IP address is whitelisted in MongoDB Atlas")
        print("3. Check if your username and password are correct")
        print("4. Ensure your network allows connections to MongoDB Atlas")
        print("5. Verify the database name in the connection string")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)

