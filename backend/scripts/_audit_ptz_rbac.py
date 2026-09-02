"""PTZ RBAC/ACL audit — operator denied on unassigned camera."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from app.core.database import camera_collection, user_collection
from app.services.session_service import SESSION_COOKIE_NAME, create_session


async def session_for(username: str) -> str | None:
    user = await user_collection.find_one({"name": username})
    if not user:
        return None
    return await create_session(str(user["_id"]))


async def ptz_stop(session: aiohttp.ClientSession, cam_id: str) -> tuple[int, dict]:
    async with session.post(f"http://127.0.0.1:10000/api/ptz/{cam_id}/stop") as resp:
        return resp.status, await resp.json()


async def main() -> None:
    ptz = await camera_collection.find_one({"ptz": True, "is_active": {"$ne": False}})
    ptz_id = str(ptz["_id"])
    print(f"ptz_cam={ptz.get('ip_address')} id={ptz_id}")

    for username in ("admin123", "controlroom", "ispit"):
        token = await session_for(username)
        if not token:
            print(f"{username}: user not found")
            continue
        headers = {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            st, body = await ptz_stop(session, ptz_id)
            print(f"{username}: stop status={st} body={body}")


if __name__ == "__main__":
    asyncio.run(main())
