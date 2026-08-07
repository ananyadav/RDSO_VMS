#!/usr/bin/env python3
"""Set MongoDB system_settings.storage.recordings_dir to the Linux deploy path.

Uses the project's .env MONGODB_URI (Atlas). Safe to re-run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import database  # noqa: E402

DEFAULT_LINUX = "/home/vms/cctv_ananya/CCTV/Recordings"


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--path", default=DEFAULT_LINUX, help="Linux recordings absolute path")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    col = database.get_collection("system_settings")
    before = await col.find_one({"_id": "storage"}) or {}
    print("BEFORE:", before.get("recordings_dir"))
    print("TARGET:", args.path)
    if args.dry_run:
        return
    await col.update_one(
        {"_id": "storage"},
        {
            "$set": {
                "recordings_dir": args.path,
                "retention_days": float(before.get("retention_days") or 10),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
        upsert=True,
    )
    after = await col.find_one({"_id": "storage"})
    print("AFTER:", after.get("recordings_dir"))


if __name__ == "__main__":
    asyncio.run(main())
