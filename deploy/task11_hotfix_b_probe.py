"""
Task 11 Hotfix B — probe affected cameras: worker, stream names, go2rtc producer state.
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection, user_collection  # noqa: E402
from app.services.session_service import SESSION_COOKIE_NAME, create_session  # noqa: E402

IPS = ["192.168.11.27", "192.168.11.30", "192.168.11.31", "192.168.11.40"]
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.17.150"


async def cookie() -> str:
    user = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not user:
        user = await user_collection.find_one({})
    token = await create_session(str(user["_id"]))
    return f"{SESSION_COOKIE_NAME}={token}"


def fetch(url: str, cookie_hdr: str | None = None, timeout=15) -> tuple[int, str]:
    headers = {"Cookie": cookie_hdr} if cookie_hdr else {}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


async def main() -> None:
    c = await cookie()
    status_code, status_raw = fetch(f"{BASE}/api/go2rtc/status", c)
    status = json.loads(status_raw)
    workers = {w["workerId"]: w for w in status.get("workers") or []}

    out = {"cameras": [], "workers": workers}
    for ip in IPS:
        cam = await camera_collection.find_one(
            {"ip_address": ip},
            {
                "_id": 1,
                "uid": 1,
                "camera_uid": 1,
                "ip_address": 1,
                "worker_id": 1,
                "workerId": 1,
                "online": 1,
                "is_active": 1,
                "name": 1,
            },
        )
        if not cam:
            out["cameras"].append({"ip": ip, "error": "not in mongo"})
            continue
        uid = cam.get("camera_uid") or cam.get("uid") or f"ip_{ip.replace('.', '_')}"
        wid = cam.get("worker_id") or cam.get("workerId") or 1
        w = workers.get(int(wid), {})
        api_port = w.get("apiPort", 1983 + int(wid))
        sub = f"{uid}_sub"
        main = f"{uid}_main"
        entry = {
            "ip": ip,
            "id": str(cam["_id"]),
            "uid": uid,
            "workerId": wid,
            "online": cam.get("online"),
            "sub_stream": sub,
            "main_stream": main,
            "grid_profile": "sub / ch102",
            "fullscreen_profile": "main / ch101 (default FullscreenCameraModal)",
        }
        # go2rtc stream info via backend proxy (auth)
        for label, stream in [("sub", sub), ("main", main)]:
            code, body = fetch(f"{BASE}/media/w{wid}/api/streams?src={stream}", c)
            entry[f"go2rtc_{label}_http"] = code
            if code == 200:
                try:
                    data = json.loads(body)
                    entry[f"go2rtc_{label}"] = data.get(stream) or data
                except json.JSONDecodeError:
                    entry[f"go2rtc_{label}_raw"] = body[:300]
        out["cameras"].append(entry)

    # Also pick one working neighbor from same group if possible
    working = await camera_collection.find_one(
        {"ip_address": "192.168.11.28", "online": True},
        {"uid": 1, "camera_uid": 1, "worker_id": 1, "ip_address": 1},
    )
    if working:
        uid = working.get("camera_uid") or working.get("uid")
        wid = working.get("worker_id") or 3
        code, body = fetch(f"{BASE}/media/w{wid}/api/streams?src={uid}_sub", c)
        out["working_neighbor_28"] = {"uid": uid, "workerId": wid, "http": code, "body": body[:500] if code != 200 else json.loads(body)}

    path = ROOT / "deploy" / "task11-hotfix-b-probe.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
