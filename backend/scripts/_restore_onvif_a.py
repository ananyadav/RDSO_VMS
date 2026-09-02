import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.services.stream_profile_service import apply_camera_stream_profile, get_camera_stream_profile


async def main() -> None:
    r = await apply_camera_stream_profile("6a6ad1d5ab17995b58f6cb92", {"sub": {"fps": 25}})
    print("restore A", r)
    p = await get_camera_stream_profile("6a6ad1d5ab17995b58f6cb92")
    print("A sub fps", p["sub"]["current"]["fps"])
    b = await get_camera_stream_profile("6a6ad1b4ab17995b58f6cb90")
    print("B sub fps", b["sub"]["current"]["fps"])


if __name__ == "__main__":
    asyncio.run(main())
