#!/usr/bin/env python3
"""Safe acceptance for RDSO 18.1.23 — write + query a redacted audit row (no camera disconnect)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> int:
    from app.services.audit_service import (
        ACTION_EVENT_DISPLAY_RESET,
        ensure_audit_indexes,
        query_audit_logs,
        write_audit,
    )

    await ensure_audit_indexes()
    actor = {"_id": "accept-18-1-23", "name": "accept", "role": "SUPER_ADMIN"}
    ok = await write_audit(
        action=ACTION_EVENT_DISPLAY_RESET,
        actor=actor,
        resource_type="event",
        resource_id="accept-event",
        resource_label="18.1.23 acceptance",
        success=True,
        metadata={
            "camera_id": "accept-cam",
            "password": "must-not-persist",
            "token": "must-not-persist",
            "main_rtsp_url": "rtsp://admin:secret@192.168.41.90/stream",
            "acceptance": "18.1.23",
        },
    )
    assert ok, "audit write failed"
    print("PASS: audit write persisted")

    page = await query_audit_logs(action=ACTION_EVENT_DISPLAY_RESET, camera_id="accept-cam", limit=5)
    assert page["total"] >= 1, page
    hit = next((i for i in page["items"] if i.get("resource_id") == "accept-event"), None)
    assert hit, page
    meta = hit.get("metadata") or {}
    assert meta.get("password") == "[REDACTED]"
    assert meta.get("token") == "[REDACTED]"
    assert "secret" not in str(meta.get("main_rtsp_url"))
    assert meta.get("camera_id") == "accept-cam"
    assert hit.get("actor_user_id") == "accept-18-1-23"
    assert hit.get("timestamp")
    print("PASS: query/filter by action+camera; secrets redacted")
    print("ACCEPTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
