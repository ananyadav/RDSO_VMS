"""Fast fleet streaming diagnostic — no TCP scan."""
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


async def main() -> None:
    import aiohttp
    from app.core.database import camera_collection, user_collection
    from app.services.camera_management import _load_go2rtc_context, apply_stream_online_status
    from app.services.go2rtc_service import get_go2rtc_status
    from app.services.stream_issues import ISSUE_LABELS

    total = await camera_collection.count_documents({})
    active = await camera_collection.count_documents({"is_active": True})

    alarm = await camera_collection.count_documents({"stream_health_alarm": True})
    not_ok = await camera_collection.count_documents({"stream_health_ok": False})
    by_cat = Counter()
    async for doc in camera_collection.find({"stream_health_alarm": True}, {"stream_health_category": 1}):
        by_cat[doc.get("stream_health_category") or "unknown"] += 1

    _, live_rows = await _load_go2rtc_context()
    items = []
    async for cam in camera_collection.find({"is_active": {"$ne": False}}, {"_id": 1, "camera_uid": 1, "is_active": 1}).limit(2000):
        items.append({"id": str(cam["_id"]), "cameraUid": cam.get("camera_uid") or "", "is_active": True})
    apply_stream_online_status(items, live_rows, playable_for_live=True)
    not_playable = sum(1 for i in items if not i.get("online"))
    confirmed = sum(1 for i in items if i.get("confirmedOffline"))

    st = await get_go2rtc_status()
    admin = await user_collection.find_one({"role": "Admin"}) or await user_collection.find_one({})
    uid = str(admin["_id"])

    print("=== INFRA ===")
    print(f"backend ok | cameras={total} active={active}")
    print(f"go2rtc running={st.get('running')} streams={st.get('streamCount')}")
    for w in st.get("workers") or []:
        print(f"  worker {w.get('workerId')}: live={w.get('liveStreamCount')} assigned={w.get('assignedCameraCount')}")

    print("\n=== STREAM HEALTH (MongoDB) ===")
    print(f"stream_health_alarm={alarm} stream_health_ok=false={not_ok}")
    for cat, n in by_cat.most_common(10):
        print(f"  {n:4d} {cat} — {ISSUE_LABELS.get(cat, cat)}")

    print("\n=== LIVE VIEW (sample {0} cams) ===".format(len(items)))
    print(f"not_playable={not_playable} confirmed_offline={confirmed} playable={len(items)-not_playable}")

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "http://127.0.0.1:10000/api/go2rtc/diagnostics",
            headers={"X-User-Id": uid},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            body = await resp.json() if resp.status == 200 else {}
            print("\n=== GO2RTC DIAGNOSTICS API ===")
            print(f"status={resp.status} streams={len(body.get('streams') or [])} missing={len(body.get('missingInGo2rtc') or body.get('missingStreams') or [])}")
            issues = body.get("healthSummary") or body.get("summary") or {}
            if issues:
                print("summary:", issues)


if __name__ == "__main__":
    asyncio.run(main())
