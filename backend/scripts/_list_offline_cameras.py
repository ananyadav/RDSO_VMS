"""List confirmed-offline cameras with health messages."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


async def main() -> None:
    from app.core.database import camera_collection
    from app.services.camera_management import _load_go2rtc_context, apply_stream_online_status

    _, live_rows = await _load_go2rtc_context()
    offline = []
    async for cam in camera_collection.find({"is_active": {"$ne": False}}):
        item = {"id": str(cam["_id"]), "is_active": True}
        apply_stream_online_status([item], live_rows, playable_for_live=True)
        if not item.get("confirmedOffline"):
            continue
        cid = str(cam["_id"])
        row = live_rows.get(cid) or live_rows.get(cam.get("camera_uid") or "") or {}
        offline.append(
            (
                cam.get("ip_address") or "",
                cam.get("worker_id"),
                row.get("issueMessage") or row.get("message") or cam.get("stream_health_message") or "offline",
            )
        )
    offline.sort(key=lambda x: x[0])
    print(f"Confirmed offline: {len(offline)}\n")
    for ip, wid, msg in offline[:40]:
        print(f"{ip}\tw{wid}\t{msg[:100]}")
    if len(offline) > 40:
        print(f"... and {len(offline) - 40} more")


if __name__ == "__main__":
    asyncio.run(main())
