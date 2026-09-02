"""Short PTZ audit probe — admin session, basic moves + non-PTZ denial."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp

from app.core.database import camera_collection, user_collection
from app.services.session_service import SESSION_COOKIE_NAME, create_session


async def main() -> None:
    ptz = await camera_collection.find_one(
        {"ptz": True, "is_active": {"$ne": False}},
        {"ip_address": 1, "name": 1, "protocol": 1, "brand": 1},
    )
    fixed = await camera_collection.find_one(
        {"ptz": {"$ne": True}, "is_active": {"$ne": False}, "ip_address": "192.168.11.26"},
        {"ip_address": 1, "name": 1, "ptz": 1},
    )
    if not fixed:
        fixed = await camera_collection.find_one(
            {"ptz": {"$ne": True}, "is_active": {"$ne": False}},
            {"ip_address": 1, "name": 1, "ptz": 1},
        )

    admin = await user_collection.find_one({"name": "admin123"})
    if not admin or not ptz or not fixed:
        print("missing admin/ptz/fixed camera")
        return

    token = await create_session(str(admin["_id"]))
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={token}", "Content-Type": "application/json"}
    ptz_id = str(ptz["_id"])
    fixed_id = str(fixed["_id"])
    print(f"PTZ {ptz['ip_address']} id={ptz_id} protocol={ptz.get('protocol')}")
    print(f"FIXED {fixed['ip_address']} id={fixed_id} ptz={fixed.get('ptz')}")

    async with aiohttp.ClientSession(headers=headers) as session:
        async def move(cam: str, direction: str) -> tuple[int, dict]:
            async with session.post(
                f"http://127.0.0.1:10000/api/ptz/{cam}/move",
                json={"direction": direction, "speed": 1},
            ) as resp:
                return resp.status, await resp.json()

        async def stop(cam: str) -> tuple[int, dict]:
            async with session.post(f"http://127.0.0.1:10000/api/ptz/{cam}/stop") as resp:
                return resp.status, await resp.json()

        for direction in ("left", "right", "up", "down", "zoom_in", "zoom_out"):
            st, body = await move(ptz_id, direction)
            await asyncio.sleep(0.25)
            st2, body2 = await stop(ptz_id)
            ok = body.get("ok", False)
            err = body.get("error", "")
            print(f"{direction:9} move={st} ok={ok} err={err!r} stop={st2} ok2={body2.get('ok')}")

        st, body = await move(fixed_id, "left")
        print(f"fixed_left status={st} body={body}")


if __name__ == "__main__":
    asyncio.run(main())
