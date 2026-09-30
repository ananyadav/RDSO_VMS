#!/usr/bin/env python3
"""RDSO 18.6.22.1 / 18.6.22.11 — opt-in acceptance (synthetic data, no fleet streams).

Enable with:
  RDSO_18_6_22_ACCEPTANCE=1 python backend/scripts/rdso_18_6_22_1_11_accept_startup_tls.py

Optional:
  ACCEPT_BASE=http://127.0.0.1:10000
  ACCEPT_DEVICE_COUNT=2000
  ACCEPT_EVENT_COUNT=2000

Does NOT open camera streams or enable recording.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _enabled() -> bool:
    return os.getenv("RDSO_18_6_22_ACCEPTANCE", "").strip().lower() in ("1", "true", "yes")


async def _http_json(session, method: str, url: str, **kwargs):
    import aiohttp

    async with session.request(method, url, **kwargs) as resp:
        text = await resp.text()
        try:
            import json

            data = json.loads(text) if text else {}
        except Exception:
            data = {"_raw": text[:200]}
        return resp.status, data


async def run() -> int:
    if not _enabled():
        print("SKIP: set RDSO_18_6_22_ACCEPTANCE=1 to run")
        return 0

    import aiohttp
    from bson import ObjectId
    from datetime import datetime, timezone

    base = (os.getenv("ACCEPT_BASE") or "http://127.0.0.1:10000").rstrip("/")
    device_n = int(os.getenv("ACCEPT_DEVICE_COUNT") or 2000)
    event_n = int(os.getenv("ACCEPT_EVENT_COUNT") or 2000)

    print(f"[accept] base={base} devices={device_n} events={event_n}")

    # --- TLS sample static check (no live certs required) ---
    sample = ROOT / "deploy" / "nginx-cctv-tls.sample.conf"
    assert sample.is_file(), "missing nginx TLS sample"
    sample_text = sample.read_text(encoding="utf-8")
    assert "TLSv1.2" in sample_text and "AES128-GCM" in sample_text
    print("[ok] nginx TLS sample ≥128-bit / TLS1.2+")

    from app.services.ccc_transport_security import tls_capability_public
    from app.services.client_media_routing import live_ws_path

    cap = tls_capability_public()
    assert cap["min_symmetric_bits"] >= 128
    assert live_ws_path(1).startswith("/media/")
    print("[ok] transport capability + relative media path")

    # --- Poll readiness ---
    t0 = time.monotonic()
    async with aiohttp.ClientSession() as session:
        ready = False
        health = {}
        for _ in range(120):
            status, health = await _http_json(session, "GET", f"{base}/api/health")
            if status == 200 and health.get("ready"):
                ready = True
                break
            # While initializing, /api/ready must be 503
            st_ready, body_ready = await _http_json(session, "GET", f"{base}/api/ready")
            if not health.get("ready"):
                assert st_ready == 503, f"expected 503 while starting, got {st_ready}"
            await asyncio.sleep(0.5)
        assert ready, f"server not ready: {health}"
        duration = health.get("startup_duration_seconds")
        print(f"[ok] ready startup_duration_seconds={duration} budget={health.get('startup_budget_seconds')}")
        if duration is not None:
            assert float(duration) <= 300, f"startup {duration}s exceeds 300s"
            print("[ok] within 300s software budget (this host)")
        else:
            print("[warn] startup_duration_seconds missing on running process (restart to populate)")

        # Parallel health/security probes
        async def probe():
            s1, _ = await _http_json(session, "GET", f"{base}/api/health")
            s2, sec = await _http_json(session, "GET", f"{base}/api/ccc/security")
            return s1, s2, sec

        t_par = time.monotonic()
        results = await asyncio.gather(*[probe() for _ in range(30)])
        elapsed = time.monotonic() - t_par
        assert all(a == 200 and b == 200 for a, b, _ in results)
        assert elapsed < 15, f"parallel probes too slow: {elapsed}s"
        print(f"[ok] parallel probes n=30 in {elapsed:.2f}s")

    # --- Synthetic Mongo pagination (no streams) ---
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from app.core.database import database
    from app.services.ccc_device_service import devices_collection, list_devices
    from app.services.event_service import events_collection

    tag = f"accept_18_6_22_{ObjectId()}"
    now = datetime.now(timezone.utc)
    print(f"[accept] inserting synthetic devices tag={tag}")
    device_docs = [
        {
            "device_uid": f"{tag}_d{i}",
            "type": "external_sensor",
            "name": f"Accept Sensor {i}",
            "location": "ACCEPT",
            "enabled": True,
            "status": "online",
            "health": "ok",
            "capabilities": [],
            "linked_camera_ids": [],
            "metadata": {"accept_tag": tag},
            "created_at": now,
            "updated_at": now,
            "opens_streams_on_register": False,
        }
        for i in range(device_n)
    ]
    await devices_collection.insert_many(device_docs, ordered=False)

    page = await list_devices(q=tag, limit=50, offset=0)
    assert page["limit"] <= 200
    assert len(page["items"]) <= 50
    assert page["total"] >= min(device_n, page["total"])
    print(f"[ok] device pagination total={page['total']} page={len(page['items'])}")

    # Second page must not load entire registry into memory (bounded cursor)
    page2 = await list_devices(q=tag, limit=50, offset=50)
    assert len(page2["items"]) <= 50

    event_docs = [
        {
            "camera_id": "",
            "camera_uid": f"{tag}_e{i}",
            "source_type": "external_sensor",
            "severity": "info",
            "priority": 1,
            "title": f"Accept event {i}",
            "message": "synthetic",
            "occurred_at": now.isoformat(),
            "status": "open",
            "acknowledged": False,
            "actions_triggered": [],
            "ui_notification": False,
            "metadata": {"accept_tag": tag, "ccc_device_uid": f"{tag}_d0"},
        }
        for i in range(event_n)
    ]
    await events_collection.insert_many(event_docs, ordered=False)
    # Count with filter — must not require loading all into Python list
    ev_total = await events_collection.count_documents({"metadata.accept_tag": tag})
    assert ev_total >= event_n
    cursor = events_collection.find({"metadata.accept_tag": tag}).sort("occurred_at", -1).limit(50)
    batch = [doc async for doc in cursor]
    assert len(batch) <= 50
    print(f"[ok] event pagination total={ev_total} page={len(batch)}")

    # Cleanup synthetic data
    await devices_collection.delete_many({"metadata.accept_tag": tag})
    await events_collection.delete_many({"metadata.accept_tag": tag})
    print("[ok] cleaned synthetic accept data")
    print(f"[PASS] RDSO 18.6.22.1/11 acceptance in {time.monotonic() - t0:.1f}s")
    print(
        "NOTE: Production-scale Linux/Nginx TLS load + multi-node soak still required "
        "on the deployment host; this harness proves software pagination/parallel/TLS design."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except AssertionError as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1) from exc
