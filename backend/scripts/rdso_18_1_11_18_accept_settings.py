"""Safe acceptance for RDSO 18.1.11.8 / 18.1.18 settings catalog."""

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
    from app.services import recording_schedule_store as recording_sched
    from app.services.settings_catalog import build_settings_catalog, filter_catalog
    from app.services.storage_settings_store import get_storage_settings_public

    count = await camera_collection.count_documents({})
    catalog = build_settings_catalog(
        camera_count=int(count),
        master_enabled=bool(recording_sched.master_enabled),
    )
    assert catalog["total"] >= 8
    scopes = {i["scope"] for i in catalog["items"]}
    assert scopes >= {"vms_server", "recording_server", "camera", "client"}
    print("PASS: catalog scopes", sorted(scopes), "total", catalog["total"])

    blob = json.dumps(catalog)
    assert "mongodb://" not in blob.lower()
    assert "rtsp://admin:" not in blob.lower()
    print("PASS: no credentials in catalog JSON")

    editable = filter_catalog(catalog, editable=True)
    assert editable["total"] >= 1
    print("PASS: editable filter", editable["total"])

    storage = get_storage_settings_public()
    assert "recordings_dir" in storage
    assert storage.get("retention_editable") is True
    print("PASS: storage settings public (persisted store)")
    print("ACCEPTANCE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
