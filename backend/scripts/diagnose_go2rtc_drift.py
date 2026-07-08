import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")


async def main() -> None:
    from app.core.database import camera_collection
    from app.services.go2rtc_service import (
        GO2RTC_API_URL,
        build_all_streams_config,
        fetch_go2rtc_streams,
        _merged_worker_streams,
    )
    from app.services.go2rtc_workers import WORKERS_ENABLED, list_active_workers, worker_base_url

    active = await camera_collection.count_documents(
        {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
    )
    inactive = await camera_collection.count_documents({"is_active": False})
    total = await camera_collection.count_documents({})
    print("DB total", total, "active", active, "inactive", inactive)

    built = await build_all_streams_config()
    desired = set((built.get("streams") or {}).keys())
    print("Config streams (active only)", len(desired), "cameras", built.get("cameraCount"))

    if WORKERS_ENABLED:
        workers = await list_active_workers()
        print("Workers in DB", len(workers))
        for w in workers:
            wid = int(w["worker_id"])
            url = worker_base_url(wid)
            streams = await fetch_go2rtc_streams(url)
            n = len(streams) if isinstance(streams, dict) else 0
            print(
                f"  worker {wid} api={url} live_streams={n} "
                f"assigned_count={w.get('assigned_camera_count')}"
            )
        merged = await _merged_worker_streams()
        print("Merged worker API streams", len(merged))
    else:
        merged = await fetch_go2rtc_streams(GO2RTC_API_URL)
        print("Legacy API streams", len(merged) if isinstance(merged, dict) else 0)

    if isinstance(merged, dict):
        stale = sorted(set(merged.keys()) - desired)
        missing = sorted(desired - set(merged.keys()))
        print("Stale in go2rtc (not in active DB config)", len(stale))
        print("Missing from go2rtc", len(missing))
        if stale[:3]:
            print("  stale sample", stale[:3])
        if missing[:3]:
            print("  missing sample", missing[:3])

    legacy_runtime = await fetch_go2rtc_streams(GO2RTC_API_URL)
    if isinstance(legacy_runtime, dict):
        print("Default GO2RTC_API_URL stream count", len(legacy_runtime))


if __name__ == "__main__":
    asyncio.run(main())
