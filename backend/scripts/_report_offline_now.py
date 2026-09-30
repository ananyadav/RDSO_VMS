import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, ".")
load_dotenv(Path("..") / ".env")
from app.core.database import camera_collection

async def main():
    cams = [c async for c in camera_collection.find(
        {"stream_health_alarm": True, "is_active": {"$ne": False}},
        {"ip_address":1,"camera_name":1,"location_path":1,"stream_health_message":1,"stream_health_category":1}
    )]
    by_cat = Counter()
    by_loc = Counter()
    samples = defaultdict(list)
    for c in cams:
        msg = (c.get("stream_health_message") or "").lower()
        cat = c.get("stream_health_category") or "unknown"
        if "wrong user" in msg or "password" in msg or "401" in msg or "unauthorized" in msg:
            reason = "wrong_password"
        elif "timeout" in msg or "no frame" in msg or "eof" in msg:
            reason = "no_frame"
        elif "refused" in msg or "unreachable" in msg or "no route" in msg or "timed out" in msg or "connect" in msg or "tcp" in msg:
            reason = "unreachable"
        elif "setup" in msg:
            reason = "rtsp_setup"
        else:
            reason = cat or "other"
        by_cat[reason] += 1
        loc = c.get("location_path") or "?"
        by_loc[loc] += 1
        if len(samples[reason]) < 8:
            samples[reason].append({
                "ip": c.get("ip_address"),
                "name": (c.get("camera_name") or "")[:40],
                "loc": loc,
                "msg": (c.get("stream_health_message") or "")[:80],
            })
    print("TOTAL", len(cams))
    print("BY_REASON", json.dumps(dict(by_cat), indent=2))
    print("BY_LOCATION", json.dumps(dict(by_loc.most_common()), indent=2))
    print("SAMPLES", json.dumps(dict(samples), indent=2))

asyncio.run(main())
