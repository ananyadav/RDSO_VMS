import json
from pathlib import Path

d = json.loads(Path("deploy/task12b-soak.json").read_text(encoding="utf-8"))
print("duration", d.get("durationSec"))
print("traversed", len(d.get("camerasTraversed") or []))
print("ws", json.dumps(d.get("ws")))
print("workersBefore", json.dumps(d.get("workersBefore")))
print("workersAfter", json.dumps(d.get("workersAfter")))
sec = d.get("security", {})
print(
    "security",
    json.dumps(
        {
            k: (
                {kk: v[kk] for kk in v if kk != "body"}
                if isinstance(v, dict)
                else v
            )
            for k, v in sec.items()
        }
    ),
)
walls = d["phases"]["walls"]
print("workersHit", walls.get("workersHit"))
for k, v in walls.items():
    if k == "workersHit":
        continue
    s = v.get("settle", {})
    a = v.get("after20s", {})
    print(
        f"WALL {k} group={v.get('group')} total={s.get('total')} mounted={s.get('mounted')} eligible={s.get('eligible')} playing={s.get('playing')} conn={s.get('connecting')} afterPlaying={a.get('playing')} afterConn={a.get('connecting')} blackCand={len(v.get('permanentBlackCandidates') or [])}"
    )
    print("  black", v.get("permanentBlackCandidates"))
sc = d["phases"]["scroll"]
print(
    "SCROLL cycles",
    sc.get("cycles"),
    "steps",
    sc.get("stepEvents"),
    "unique",
    sc.get("uniqueIpsSeen"),
    "afterExtra",
    sc.get("uniqueIpsAfterExtraGroups"),
)
a = sc["afterSettle"]
print(
    "afterSettle",
    {k: a[k] for k in ["mounted", "eligible", "playing", "connecting", "total", "onlineNoVideo"]},
)
vc = d["phases"]["viewChange"]
print("VIEW cycles", vc["cycles"])
plays = [c.get("gridPlaying") for c in vc["detail"]]
backs = [c.get("backPlaying") for c in vc["detail"]]
fs = [c.get("fullscreen") for c in vc["detail"]]
print("gridPlaying min/max", min(plays), max(plays), "back min/max", min(backs), max(backs))
print("fs videoWidth>0", sum(1 for x in fs if x and (x.get("videoWidth") or 0) > 0), "/", len(fs))
print("fs profiles", [x.get("profile") if x else None for x in fs[:8]])
print("PTZ", json.dumps({k: d["phases"]["ptz"][k] for k in d["phases"]["ptz"] if k != "gridDuring"}))
print("PTZ grid", d["phases"]["ptz"].get("gridDuring"))
print("SETTLED", json.dumps(d["phases"]["settled"]))
print("health n", len(d["apiHealth"]))
print("healthMs", [h["healthMs"] for h in d["apiHealth"]])
print("mongodb", set(str(h.get("health", {}).get("mongodb")) for h in d["apiHealth"]))
print("ready", set(str(h.get("health", {}).get("ready")) for h in d["apiHealth"]))
print("cameraCount", set(str(h.get("health", {}).get("cameraCount")) for h in d["apiHealth"]))
mem = d["samples"]
print("MEM")
for s in mem:
    h = s.get("heap") or {}
    m = s.get("metrics") or {}
    print(s["elapsedSec"], s["label"], h.get("usedMB"), "nodes", m.get("nodes"))
