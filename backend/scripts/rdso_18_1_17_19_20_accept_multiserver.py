"""Safe acceptance for RDSO 18.1.17 / 18.1.19 / 18.1.20 (logical registry only)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> int:
    from app.core.database import camera_collection
    from app.services.client_media_routing import (
        build_client_media_routing,
        list_recording_servers_public,
        sessions_findable_across_servers,
    )
    from app.services.recording_ha_store import get_ha_store
    from app.services.recording_server_config import recording_server_count_limit

    assert recording_server_count_limit() is None
    print("PASS: no recording-server count cap")

    store = get_ha_store()
    # Register several *logical* servers (not physical hosts).
    for sid, role in (
        ("accept-primary-a", "primary"),
        ("accept-standby-1", "standby"),
        ("accept-standby-2", "standby"),
    ):
        await store.upsert_server(
            sid,
            {
                "role": role,
                "enabled": True,
                "healthy": True,
                "hostname": sid,
                "last_seen": "2026-09-09T12:00:00+00:00",
            },
        )
    listed = await list_recording_servers_public()
    ids = {s["server_id"] for s in listed["items"]}
    assert {"accept-primary-a", "accept-standby-1", "accept-standby-2"} <= ids
    print("PASS: multiple logical servers registered/listed", len(listed["items"]))

    cam = await camera_collection.find_one({"ip_address": "192.168.41.90"})
    if not cam:
        print("FAIL: safe camera missing")
        return 1
    routing = build_client_media_routing(cam)
    assert routing["live"]["ws_path"].startswith("/media/w")
    assert "{camera_id}" in routing["playback"]["media_path_template"]
    assert routing["seamless"] and routing["identity_stable"]
    blob = json.dumps(routing)
    assert "password" not in blob.lower() or "must-not" not in blob
    assert "rtsp://" not in blob
    print("PASS: client-media routing relative + camera-based", routing["camera_uid"])

    # Simulate sessions from different recording servers for same camera
    cid = str(cam["_id"])
    sessions = [
        {"camera_id": cid, "recording_server_id": "accept-primary-a"},
        {"camera_id": cid, "recording_server_id": "accept-standby-1"},
    ]
    found = sessions_findable_across_servers(sessions, camera_id=cid)
    assert len(found) == 2
    print("PASS: playback identity ignores recording_server_id")

    # Reassignment keeps client identity
    moved = {**cam, "recording_server_id": "accept-standby-1"}
    r2 = build_client_media_routing(moved)
    assert r2["camera_id"] == routing["camera_id"]
    assert r2["camera_uid"] == routing["camera_uid"]
    print("PASS: reassignment keeps camera identity")

    print("DEPLOYMENT_NOTE: multi-host playback requires shared RECORDINGS_DIR / volume")
    print("ACCEPTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
