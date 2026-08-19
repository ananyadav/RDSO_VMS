"""Find a Hikvision PTZ camera for a short under-load control check."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
from app.core.database import camera_collection  # noqa: E402


async def main() -> None:
    cam = await camera_collection.find_one(
        {
            "ptz": True,
            "is_active": {"$ne": False},
            "$or": [
                {"brand": {"$regex": "hik", "$options": "i"}},
                {"protocol": {"$regex": "hik", "$options": "i"}},
                {"manufacturer": {"$regex": "hik", "$options": "i"}},
            ],
        },
        {"ip_address": 1, "camera_group": 1, "brand": 1, "protocol": 1, "ptz": 1, "name": 1},
    )
    if not cam:
        cam = await camera_collection.find_one(
            {"ptz": True, "is_active": {"$ne": False}},
            {"ip_address": 1, "camera_group": 1, "brand": 1, "protocol": 1, "name": 1},
        )
        print("fallback any ptz")
    if cam:
        cam["_id"] = str(cam["_id"])
    print(json.dumps(cam, default=str, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
