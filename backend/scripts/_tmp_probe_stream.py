import asyncio
import json
import urllib.parse
import urllib.request

from app.database import camera_collection
from app.services.go2rtc_workers import worker_base_url


def _get(url: str, timeout: int = 25) -> tuple[int, bytes]:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


async def main() -> None:
    c = await camera_collection.find_one(
        {"camera_uid": "ip_192_168_11_25"},
        {"camera_uid": 1, "worker_id": 1, "name": 1, "online": 1},
    )
    if not c:
        c = await camera_collection.find_one(
            {"is_active": {"$ne": False}, "worker_id": {"$exists": True}},
            {"camera_uid": 1, "worker_id": 1, "name": 1, "online": 1},
        )
    if not c:
        print("no camera")
        return
    uid = c["camera_uid"]
    wid = c["worker_id"]
    stream = f"{uid}_sub"
    base = worker_base_url(wid)
    status, body = _get(f"{base}/api/streams")
    data = json.loads(body.decode())
    info = data.get(stream)
    print("camera", c.get("name"), "uid", uid, "worker", wid, "online", c.get("online"))
    if info:
        print("producers", len(info.get("producers") or []), "consumers", len(info.get("consumers") or []))
        for p in (info.get("producers") or [])[:2]:
            print(" producer", json.dumps(p)[:300])
    else:
        print("stream not in go2rtc")
    q = urllib.parse.urlencode({"src": stream})
    fr_status, fr_body = _get(f"{base}/api/frame.jpeg?{q}", timeout=30)
    print("frame status", fr_status, "bytes", len(fr_body))


if __name__ == "__main__":
    asyncio.run(main())
