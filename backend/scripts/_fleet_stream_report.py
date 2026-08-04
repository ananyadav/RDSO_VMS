"""One-shot fleet + stream health report (run from backend/)."""
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


async def main() -> None:
    from app.core.database import camera_collection
    from app.services.camera_management import _load_go2rtc_context, apply_stream_online_status
    from app.services.go2rtc_service import get_go2rtc_status
    from app.services.stream_health import stream_health_snapshot
    from app.services.stream_issues import ISSUE_LABELS

    total = await camera_collection.count_documents({})
    active = await camera_collection.count_documents({"is_active": {"$ne": False}})

    snap = stream_health_snapshot()
    rows = snap.get("rows") or []
    by_cat = Counter()
    confirmed = 0
    for r in rows:
        cat = r.get("issueCategory") or "unknown"
        if r.get("confirmedOffline"):
            confirmed += 1
            by_cat[cat] += 1

    _, live_rows = await _load_go2rtc_context()
    items = []
    async for cam in camera_collection.find({"is_active": {"$ne": False}}):
        cid = str(cam["_id"])
        items.append(
            {
                "id": cid,
                "cameraUid": cam.get("camera_uid") or "",
                "is_active": cam.get("is_active") is not False,
            }
        )
    apply_stream_online_status(items, live_rows, playable_for_live=True)
    playable = sum(1 for i in items if i.get("online"))
    confirmed_ui = sum(1 for i in items if i.get("confirmedOffline"))
    offline_ui = sum(1 for i in items if not i.get("online"))

    st = await get_go2rtc_status()
    print("=== FLEET ===")
    print(f"total={total} active={active}")
    print(f"health_rows={len(rows)} confirmed_offline={confirmed}")
    print(f"live_view: playable_online={playable} not_online={offline_ui} confirmed_offline={confirmed_ui}")
    print("\n=== CONFIRMED OFFLINE (category) ===")
    for cat, n in by_cat.most_common(20):
        label = ISSUE_LABELS.get(cat, cat)
        print(f"  {n:4d}  {cat} — {label}")

    print("\n=== GO2RTC ===")
    print(f"running={st.get('running')} streamCount={st.get('streamCount')}")
    for w in st.get("workers") or []:
        print(
            f"  worker {w.get('workerId')}: live={w.get('liveStreamCount')} "
            f"assigned={w.get('assignedCameraCount')} running={w.get('running')}"
        )

    # sample 5 confirmed offline with message
    print("\n=== SAMPLE OFFLINE CAMERAS ===")
    shown = 0
    async for cam in camera_collection.find({"is_active": {"$ne": False}}):
        uid = cam.get("camera_uid") or ""
        row = live_rows.get(uid) or live_rows.get(str(cam["_id"])) or {}
        if not row.get("confirmedOffline") and row.get("issueCategory") != "online":
            continue
        if row.get("issueCategory") == "online":
            continue
        msg = row.get("issueMessage") or row.get("lastError") or row.get("issueCategory")
        print(f"  {cam.get('ip_address')} worker={cam.get('worker_id')} — {msg}")
        shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    asyncio.run(main())
