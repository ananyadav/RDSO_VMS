"""Task 12B — pick soak locations covering go2rtc workers; dump PTZ candidates."""
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402


async def main() -> None:
    groups = defaultdict(lambda: {"total": 0, "online": 0, "workers": Counter(), "ptz": 0})
    ptz = []
    async for cam in camera_collection.find(
        {},
        {
            "ip_address": 1,
            "camera_group": 1,
            "worker_id": 1,
            "is_active": 1,
            "ptz": 1,
            "name": 1,
        },
    ):
        g = cam.get("camera_group") or "_none"
        wid = cam.get("worker_id") or 0
        groups[g]["total"] += 1
        if cam.get("is_active") is not False:
            groups[g]["online"] += 1
        groups[g]["workers"][int(wid) if wid else 0] += 1
        if cam.get("ptz"):
            groups[g]["ptz"] += 1
            ptz.append(
                {
                    "id": str(cam["_id"]),
                    "ip": cam.get("ip_address"),
                    "group": g,
                    "workerId": wid,
                }
            )

    ranked = []
    for name, info in groups.items():
        ranked.append(
            {
                "group": name,
                "total": info["total"],
                "online": info["online"],
                "w1": info["workers"][1],
                "w2": info["workers"][2],
                "w3": info["workers"][3],
                "workersPresent": sorted(k for k, v in info["workers"].items() if v and k),
                "ptz": info["ptz"],
            }
        )
    ranked.sort(key=lambda x: -x["total"])

    # Prefer large groups that include each worker, plus mixed.
    cover = {"w1": None, "w2": None, "w3": None}
    for row in ranked:
        for key, wid in (("w1", 1), ("w2", 2), ("w3", 3)):
            if cover[key] is None and row[key] >= 8:
                cover[key] = row["group"]

    mixed = next((r["group"] for r in ranked if len(r["workersPresent"]) >= 2 and r["total"] >= 20), None)
    large = next((r["group"] for r in ranked if r["total"] >= 50), ranked[0]["group"])

    out = {
        "topGroups": ranked[:20],
        "coverGroups": cover,
        "mixedGroup": mixed,
        "largeScrollGroup": large,
        "ptzSample": ptz[:15],
    }
    path = ROOT / "deploy" / "task12b-groups.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("coverGroups", "mixedGroup", "largeScrollGroup")}, indent=2))
    print("top:")
    for r in ranked[:12]:
        print(f"  {r['total']:3d} w1={r['w1']:3d} w2={r['w2']:3d} w3={r['w3']:3d} {r['group']}")


if __name__ == "__main__":
    asyncio.run(main())
