"""Ensure every go2rtc worker is running and synced. Run on the GPU server after deploy."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")


async def main() -> int:
    from app.services.go2rtc_service import is_api_healthy, fetch_go2rtc_streams
    from app.services.go2rtc_workers import (
        WORKERS_ENABLED,
        list_active_workers,
        startup_workers,
        worker_base_url,
        worker_pm2_name,
        pm2_worker_running,
    )

    if not WORKERS_ENABLED:
        print("GO2RTC_WORKERS_ENABLED=false — nothing to do")
        return 0

    print("Running startup sync (write yaml, start workers, push streams)...")
    result = await startup_workers()
    print("startup ok=", result.get("ok"), "workers=", result.get("workerCount"))

    workers = await list_active_workers()
    exit_code = 0
    for row in workers:
        wid = int(row["worker_id"])
        url = worker_base_url(wid)
        healthy = await is_api_healthy(url)
        live = len(await fetch_go2rtc_streams(url))
        pm2 = await pm2_worker_running(wid)
        assigned = row.get("assigned_camera_count")
        ok = healthy and live >= max((assigned or 0) * 2 - 4, 0)
        status = "OK" if ok else "FAIL"
        if not ok:
            exit_code = 1
        print(
            f"  [{status}] worker {wid} ({worker_pm2_name(wid)}): "
            f"api={url} healthy={healthy} pm2={pm2} live_streams={live} assigned={assigned}"
        )

    if exit_code:
        print("\nFix: pm2 restart cctv-backend && python backend/scripts/ensure_go2rtc_workers.py")
        print("Or:  pm2 start ecosystem.config.cjs --only go2rtc-worker-2")
    else:
        print("\nAll workers healthy.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
