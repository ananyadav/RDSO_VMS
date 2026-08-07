"""Create a dev session cookie for latency test harness."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import user_collection  # noqa: E402
from app.services.session_service import SESSION_COOKIE_NAME, create_session  # noqa: E402


async def main() -> None:
    user = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not user:
        user = await user_collection.find_one({})
    if not user:
        raise SystemExit("no users")
    token = await create_session(str(user["_id"]))
    print(f"{SESSION_COOKIE_NAME}={token}")


if __name__ == "__main__":
    asyncio.run(main())
