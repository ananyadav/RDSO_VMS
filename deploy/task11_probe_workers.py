"""Task 11 helpers: dump go2rtc status workers + one camera per worker from Mongo."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402
from app.services.session_service import SESSION_COOKIE_NAME, create_session  # noqa: E402
from app.core.database import user_collection  # noqa: E402


async def cookie() -> str:
    user = await user_collection.find_one({"role": {"$regex": "^admin$", "$options": "i"}})
    if not user:
        user = await user_collection.find_one({})
    token = await create_session(str(user["_id"]))
    return f"{SESSION_COOKIE_NAME}={token}"


def fetch(url: str, cookie_hdr: str) -> dict:
    req = urllib.request.Request(url, headers={"Cookie": cookie_hdr})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


async def main() -> None:
    c = await cookie()
    base = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.17.150"
    status = fetch(f"{base}/api/go2rtc/status", c)
    workers = status.get("workers") or []
    print("STATUS", json.dumps({k: status.get(k) for k in ("running", "streamCount", "cameraCount", "enabled")}, indent=2))
    for w in workers:
        print(
            f"worker {w.get('workerId')}: api={w.get('apiPort')} rtsp={w.get('rtspPort')} "
            f"webrtc={w.get('webrtcPort')} cams={w.get('assignedCameraCount')} "
            f"running={w.get('running')} streams={w.get('liveStreamCount')} base={w.get('baseUrl')}"
        )

    samples = []
    for wid in (1, 2, 3):
        cam = await camera_collection.find_one(
            {"worker_id": wid, "is_active": {"$ne": False}},
            {"uid": 1, "ip_address": 1, "name": 1, "worker_id": 1, "online": 1},
        )
        if not cam:
            cam = await camera_collection.find_one(
                {"worker_id": wid},
                {"uid": 1, "ip_address": 1, "name": 1, "worker_id": 1, "is_active": 1},
            )
        if cam:
            cam["_id"] = str(cam["_id"])
            samples.append(cam)
            print("SAMPLE", wid, cam.get("uid"), cam.get("ip_address"), cam.get("name"))
        else:
            # fallback: any camera with that worker as string
            print("SAMPLE", wid, "NONE")
    # Also count by worker_id
    for wid in (1, 2, 3):
        n = await camera_collection.count_documents({"worker_id": wid})
        n2 = await camera_collection.count_documents({"worker_id": str(wid)})
        print(f"count worker_id={wid}: {n} (str: {n2})")
    Path(ROOT / "deploy" / "task11-worker-samples.json").write_text(
        json.dumps({"cookie": c, "workers": workers, "samples": samples}, indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
