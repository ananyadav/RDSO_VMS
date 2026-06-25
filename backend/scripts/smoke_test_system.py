#!/usr/bin/env python3
"""Quick live system smoke test (backend must be running on :10000)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.core.database import user_collection  # noqa: E402


async def main() -> int:
    admin = await user_collection.find_one({"role": "Admin"}) or await user_collection.find_one({})
    if not admin:
        print("[FAIL] No users in database")
        return 1

    uid = str(admin["_id"])
    base = os.getenv("VITE_API_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
    headers = {"X-User-Id": uid}
    fails = 0

    async with aiohttp.ClientSession() as session:
        endpoints = [
            "/api/health",
            "/api/cameras",
            "/api/storage/dashboard?summary=1",
            "/api/recordings/health",
            "/api/live/diagnostics",
            "/api/go2rtc/diagnostics",
            "/api/recording/schedule",
        ]
        for path in endpoints:
            try:
                async with session.get(
                    f"{base}{path}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as resp:
                    ok = resp.status == 200
                    extra = ""
                    if ok:
                        try:
                            data = json.loads(await resp.text())
                        except json.JSONDecodeError:
                            data = {}
                        if "storage/dashboard" in path:
                            s = data.get("summary", {})
                            extra = (
                                f" recordings_gb={s.get('recordings_storage_gb')} "
                                f"segments={s.get('total_segments')} cameras={s.get('camera_count')}"
                            )
                        elif path.endswith("/cameras"):
                            extra = f" count={len(data) if isinstance(data, list) else '?'}"
                        elif path.endswith("/health") and "recordings" in path:
                            extra = (
                                f" recording={data.get('recording_count')} "
                                f"offline={data.get('offline_count')} "
                                f"healthy={data.get('healthy_count')}"
                            )
                        elif path.endswith("/schedule"):
                            sched = data.get("schedule") or data
                            on = sum(1 for v in sched.values() if v) if isinstance(sched, dict) else 0
                            extra = f" master={data.get('master_enabled')} scheduled={on}"
                    tag = "OK" if ok else "FAIL"
                    if not ok:
                        fails += 1
                    print(f"[{tag}] {path} -> {resp.status}{extra}")
            except Exception as exc:
                fails += 1
                print(f"[FAIL] {path} -> {exc}")

        gpath = os.getenv("GO2RTC_BIN", "")
        gexists = Path(gpath).is_file() if gpath else False
        print(f"[{'OK' if gexists else 'WARN'}] GO2RTC_BIN exists={gexists}")

        try:
            async with session.get(
                "http://127.0.0.1:3000/",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                print(f"[{'OK' if resp.status == 200 else 'FAIL'}] frontend :3000 -> {resp.status}")
                if resp.status != 200:
                    fails += 1
        except Exception as exc:
            print(f"[FAIL] frontend :3000 -> {exc}")
            fails += 1

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
