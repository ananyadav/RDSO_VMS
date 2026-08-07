#!/usr/bin/env python3
"""Task 10: canary ~1s GOP on Live View substream (channel 102) only."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.core.database import camera_collection  # noqa: E402
from app.services.go2rtc_workers import get_worker_id_for_camera_doc  # noqa: E402
from app.services.hikvision_ptz import _isapi  # noqa: E402

EXCLUDE_IPS = {
    "192.168.41.106",
    "192.168.41.13",
    "192.168.41.23",
    "192.168.41.24",
    "192.168.41.41",
}

ARTIFACT_DIR = ROOT / "deploy" / "task10"
FFPROBE = (
    Path(r"C:\Users\Ananya Yadav\Downloads")
    / "ffmpeg-2026-05-25-git-34dfa8bf2b-essentials_build"
    / "bin"
    / "ffprobe.exe"
)

TAGS = (
    "videoCodecType",
    "videoResolutionWidth",
    "videoResolutionHeight",
    "maxFrameRate",
    "GovLength",
    "keyFrameInterval",
    "constantBitRate",
    "vbrUpperCap",
    "videoQualityControlType",
)


def parse_fields(xml: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for tag in TAGS:
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml or "")
        out[tag] = m.group(1) if m else None
    return out


def hik_fps(max_frame_rate: str | None) -> float | None:
    """Hikvision maxFrameRate is fps * 100 (e.g. 1500 => 15)."""
    if not max_frame_rate:
        return None
    try:
        v = float(max_frame_rate)
    except ValueError:
        return None
    if v <= 0:
        return None
    # Values like 15 mean 15 fps on some firmwares; 1500 means 15.00
    if v > 100:
        return v / 100.0
    return v


def target_gop(fps: float) -> tuple[int, int]:
    """Return (GovLength frames, keyFrameInterval ms) for ~1s GOP."""
    frames = max(1, int(round(fps)))
    return frames, 1000


def replace_tag(xml: str, tag: str, value: str) -> str:
    pattern = rf"(<{tag}>)([^<]*)(</{tag}>)"
    if not re.search(pattern, xml):
        raise ValueError(f"missing <{tag}>")
    return re.sub(pattern, rf"\g<1>{value}\g<3>", xml, count=1)


def is_healthy(cam: dict) -> bool:
    if cam.get("stream_health_ok") is True:
        return True
    cat = (cam.get("stream_health_category") or "").lower()
    return cat in {"online", "ok", "healthy"}


async def load_candidates() -> list[dict]:
    rows = []
    async for cam in camera_collection.find({}):
        ip = (cam.get("ip_address") or "").strip()
        if not ip or ip in EXCLUDE_IPS:
            continue
        if not is_healthy(cam):
            continue
        wid = cam.get("worker_id")
        if wid is None:
            try:
                wid = await get_worker_id_for_camera_doc(cam)
            except Exception:  # noqa: BLE001
                wid = None
        rows.append(
            {
                "ip": ip,
                "id": str(cam["_id"]),
                "cameraUid": cam.get("camera_uid") or "",
                "model": cam.get("model") or "unknown",
                "camera_group": cam.get("camera_group") or "",
                "site": cam.get("site") or "",
                "building": cam.get("building") or "",
                "worker_id": int(wid) if wid is not None else None,
                "doc": cam,
            }
        )
    return rows


def select_canary(candidates: list[dict], target: int = 25) -> list[dict]:
    """Prefer diversity across workers, groups, and models."""
    by_worker: dict[Any, list[dict]] = defaultdict(list)
    for c in candidates:
        by_worker[c["worker_id"] or 0].append(c)

    selected: list[dict] = []
    seen_ips: set[str] = set()
    used_groups: set[str] = set()
    used_models: set[str] = set()

    # Round-robin workers first
    workers = sorted(by_worker.keys(), key=lambda w: (w == 0, w))
    idx = {w: 0 for w in workers}
    while len(selected) < target and any(idx[w] < len(by_worker[w]) for w in workers):
        progress = False
        for w in workers:
            if len(selected) >= target:
                break
            lst = by_worker[w]
            while idx[w] < len(lst):
                c = lst[idx[w]]
                idx[w] += 1
                if c["ip"] in seen_ips:
                    continue
                # Prefer new group/model when possible
                group = c["camera_group"] or c["site"] or "none"
                model = c["model"]
                prefer = (group not in used_groups) or (model not in used_models)
                # On early passes prefer diversity; later take anything
                if prefer or len(selected) >= target // 2:
                    selected.append(c)
                    seen_ips.add(c["ip"])
                    used_groups.add(group)
                    used_models.add(model)
                    progress = True
                    break
            if not progress:
                # fallback: take next from this worker anyway
                while idx[w] < len(lst) and len(selected) < target:
                    c = lst[idx[w]]
                    idx[w] += 1
                    if c["ip"] in seen_ips:
                        continue
                    selected.append(c)
                    seen_ips.add(c["ip"])
                    used_groups.add(c["camera_group"] or "none")
                    used_models.add(c["model"])
                    progress = True
                    break
        if not progress:
            break

    # Fill remaining from leftover pool
    if len(selected) < target:
        for c in candidates:
            if len(selected) >= target:
                break
            if c["ip"] in seen_ips:
                continue
            selected.append(c)
            seen_ips.add(c["ip"])
    return selected[:target]


async def inspect_one(entry: dict) -> dict:
    cam = entry["doc"]
    st102, xml102 = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
    st101, xml101 = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
    f102 = parse_fields(xml102 if st102 == 200 else "")
    f101 = parse_fields(xml101 if st101 == 200 else "")
    fps = hik_fps(f102.get("maxFrameRate"))
    gov_t, key_t = target_gop(fps or 15.0)
    return {
        "ip": entry["ip"],
        "id": entry["id"],
        "cameraUid": entry["cameraUid"],
        "workerId": entry["worker_id"],
        "model": entry["model"],
        "camera_group": entry["camera_group"],
        "site": entry["site"],
        "building": entry["building"],
        "ch102_status": st102,
        "ch101_status": st101,
        "ch102": f102,
        "ch101": f101,
        "fps": fps,
        "target_GovLength": gov_t,
        "target_keyFrameInterval": key_t,
        "xml102": xml102 if st102 == 200 else None,
    }


async def apply_one(entry: dict, inspected: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        cam = entry["doc"]
        ip = entry["ip"]
        ts = datetime.now(timezone.utc).isoformat()
        before = inspected["ch102"]
        fps = inspected["fps"] or 15.0
        gov_t, key_t = target_gop(fps)
        xml = inspected.get("xml102")
        if not xml:
            st, xml = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
            if st != 200 or not xml:
                return {
                    "ip": ip,
                    "ok": False,
                    "error": f"GET 102 failed status={st}",
                    "timestamp": ts,
                }

        # Skip if already ~1s
        try:
            cur_gov = int(before.get("GovLength") or 0)
            cur_key = int(before.get("keyFrameInterval") or 0)
        except ValueError:
            cur_gov, cur_key = 0, 0
        already = cur_gov == gov_t and abs(cur_key - key_t) <= 50

        backup = {
            "ip": ip,
            "cameraUid": entry["cameraUid"],
            "id": entry["id"],
            "workerId": entry["worker_id"],
            "model": entry["model"],
            "timestamp": ts,
            "original": {
                "GovLength": before.get("GovLength"),
                "keyFrameInterval": before.get("keyFrameInterval"),
                "videoCodecType": before.get("videoCodecType"),
                "maxFrameRate": before.get("maxFrameRate"),
                "fps": fps,
                "videoResolutionWidth": before.get("videoResolutionWidth"),
                "videoResolutionHeight": before.get("videoResolutionHeight"),
                "constantBitRate": before.get("constantBitRate"),
                "vbrUpperCap": before.get("vbrUpperCap"),
                "videoQualityControlType": before.get("videoQualityControlType"),
            },
            "target": {"GovLength": gov_t, "keyFrameInterval": key_t},
            "already_approx_1s": already,
        }

        if already:
            _, xml101_a = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
            return {
                "ip": ip,
                "ok": True,
                "skipped": True,
                "reason": "already ~1s GOP",
                "backup": backup,
                "after102": before,
                "after101": parse_fields(xml101_a),
                "ch101_unchanged": True,
                "timestamp": ts,
            }

        try:
            new_xml = replace_tag(xml, "GovLength", str(gov_t))
            new_xml = replace_tag(new_xml, "keyFrameInterval", str(key_t))
        except ValueError as e:
            return {"ip": ip, "ok": False, "error": str(e), "backup": backup, "timestamp": ts}

        # Capture 101 before for comparison
        _, xml101_b = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
        before101 = parse_fields(xml101_b)

        pst, ptxt = await _isapi(
            cam, "PUT", "/ISAPI/Streaming/channels/102", body=new_xml.encode("utf-8")
        )
        _, xml102_a = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/102")
        _, xml101_a = await _isapi(cam, "GET", "/ISAPI/Streaming/channels/101")
        after102 = parse_fields(xml102_a)
        after101 = parse_fields(xml101_a)

        # Confirm only GOP fields changed among monitored tags
        changed = []
        for tag in TAGS:
            if before.get(tag) != after102.get(tag):
                changed.append(tag)
        unexpected = [t for t in changed if t not in {"GovLength", "keyFrameInterval"}]
        ok = (
            pst == 200
            and after102.get("GovLength") == str(gov_t)
            and after102.get("keyFrameInterval") == str(key_t)
            and not unexpected
            and before101 == after101
        )
        return {
            "ip": ip,
            "ok": ok,
            "put_status": pst,
            "put_body": (ptxt or "")[:160],
            "backup": backup,
            "before102": before,
            "after102": after102,
            "changed_fields": changed,
            "unexpected_changes": unexpected,
            "before101": before101,
            "after101": after101,
            "ch101_unchanged": before101 == after101,
            "timestamp": ts,
        }


def ffprobe_gop(ip: str, user: str, password: str) -> dict:
    url = f"rtsp://{user}:{quote(password, safe='')}@{ip}:554/Streaming/Channels/102"
    cmd = [
        str(FFPROBE),
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=key_frame,pict_type,pkt_pts_time",
        "-of",
        "json",
        "-read_intervals",
        "%+6",
        url,
    ]
    info_cmd = [
        str(FFPROBE),
        "-v",
        "error",
        "-rtsp_transport",
        "tcp",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate",
        "-of",
        "json",
        url,
    ]
    out: dict[str, Any] = {"ip": ip}
    try:
        info = subprocess.run(info_cmd, capture_output=True, text=True, timeout=40)
        if info.returncode == 0:
            st = (json.loads(info.stdout or "{}").get("streams") or [{}])[0]
            out.update(
                {
                    "codec_name": st.get("codec_name"),
                    "width": st.get("width"),
                    "height": st.get("height"),
                    "avg_frame_rate": st.get("avg_frame_rate"),
                }
            )
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
        if proc.returncode != 0:
            out["error"] = (proc.stderr or "")[-300:]
            return out
        frames = json.loads(proc.stdout or "{}").get("frames") or []
        idxs = [
            i
            for i, f in enumerate(frames)
            if str(f.get("key_frame")) == "1" or f.get("pict_type") == "I"
        ]
        fps = None
        afr = out.get("avg_frame_rate") or ""
        if "/" in str(afr):
            a, b = str(afr).split("/", 1)
            try:
                fps = float(a) / float(b) if float(b) else None
            except ValueError:
                fps = None
        gaps_f = [idxs[i + 1] - idxs[i] for i in range(len(idxs) - 1)]
        gaps_s = [round(g / fps, 3) for g in gaps_f] if fps and fps > 0 else []
        out.update(
            {
                "frames": len(frames),
                "i_frames": len(idxs),
                "gaps_frames": gaps_f[:8],
                "gaps_sec_est": gaps_s[:8],
                "gap_median_sec": sorted(gaps_s)[len(gaps_s) // 2] if gaps_s else None,
                "fps_used": fps,
            }
        )
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


async def cmd_select(target: int) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = await load_candidates()
    canary = select_canary(candidates, target=target)
    summary = {
        "selected": len(canary),
        "candidate_healthy": len(candidates),
        "workers": Counter(c["worker_id"] for c in canary),
        "models": Counter(c["model"] for c in canary),
        "groups": Counter(c["camera_group"] or "none" for c in canary),
        "cameras": [
            {
                "ip": c["ip"],
                "id": c["id"],
                "cameraUid": c["cameraUid"],
                "workerId": c["worker_id"],
                "model": c["model"],
                "camera_group": c["camera_group"],
                "site": c["site"],
            }
            for c in canary
        ],
    }
    # JSON-serialize Counter
    summary["workers"] = {str(k): v for k, v in summary["workers"].items()}
    summary["models"] = dict(summary["models"])
    summary["groups"] = dict(summary["groups"])
    path = ARTIFACT_DIR / "canary-selection.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


async def cmd_apply(concurrency: int = 4) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    sel_path = ARTIFACT_DIR / "canary-selection.json"
    if not sel_path.exists():
        raise SystemExit("Run select first")
    selection = json.loads(sel_path.read_text(encoding="utf-8"))
    ips = [c["ip"] for c in selection["cameras"]]

    # Reload full docs
    entries = []
    for ip in ips:
        cam = await camera_collection.find_one({"ip_address": ip})
        if not cam:
            continue
        wid = cam.get("worker_id")
        if wid is None:
            try:
                wid = await get_worker_id_for_camera_doc(cam)
            except Exception:  # noqa: BLE001
                wid = None
        entries.append(
            {
                "ip": ip,
                "id": str(cam["_id"]),
                "cameraUid": cam.get("camera_uid") or "",
                "model": cam.get("model") or "unknown",
                "camera_group": cam.get("camera_group") or "",
                "site": cam.get("site") or "",
                "building": cam.get("building") or "",
                "worker_id": int(wid) if wid is not None else None,
                "doc": cam,
            }
        )

    print(f"Inspecting {len(entries)} cameras…", file=sys.stderr)
    inspected_list = []
    for e in entries:
        try:
            inspected_list.append(await inspect_one(e))
            print(
                f"INSPECT {e['ip']} fps={inspected_list[-1]['fps']} "
                f"gov={inspected_list[-1]['ch102'].get('GovLength')} "
                f"key={inspected_list[-1]['ch102'].get('keyFrameInterval')} "
                f"-> {inspected_list[-1]['target_GovLength']}/{inspected_list[-1]['target_keyFrameInterval']}",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            inspected_list.append({"ip": e["ip"], "error": str(exc), "ch102_status": None})
            print(f"INSPECT FAIL {e['ip']}: {exc}", file=sys.stderr)

    (ARTIFACT_DIR / "canary-inspect-before.json").write_text(
        json.dumps(
            [{k: v for k, v in r.items() if k != "xml102"} for r in inspected_list],
            indent=2,
        ),
        encoding="utf-8",
    )

    # Build inspect map with xml retained in memory
    by_ip = {r["ip"]: r for r in inspected_list if r.get("ch102_status") == 200}
    entries_ok = [e for e in entries if e["ip"] in by_ip]

    # Original GOP / FPS distributions
    dist = {
        "fps": Counter(),
        "GovLength": Counter(),
        "keyFrameInterval": Counter(),
        "codec": Counter(),
        "settings_by_fps": {},
    }
    for r in by_ip.values():
        fps = r.get("fps")
        dist["fps"][str(fps)] += 1
        dist["GovLength"][str(r["ch102"].get("GovLength"))] += 1
        dist["keyFrameInterval"][str(r["ch102"].get("keyFrameInterval"))] += 1
        dist["codec"][str(r["ch102"].get("videoCodecType"))] += 1
        gov_t, key_t = target_gop(fps or 15.0)
        dist["settings_by_fps"][str(fps)] = {"GovLength": gov_t, "keyFrameInterval": key_t}

    print(f"Applying with concurrency={concurrency}…", file=sys.stderr)
    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *[apply_one(e, by_ip[e["ip"]], sem) for e in entries_ok]
    )

    backups = [r.get("backup") for r in results if r.get("backup")]
    (ARTIFACT_DIR / "canary-rollback.json").write_text(
        json.dumps(backups, indent=2), encoding="utf-8"
    )
    (ARTIFACT_DIR / "canary-apply-results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )

    ok_n = sum(1 for r in results if r.get("ok"))
    fail_n = sum(1 for r in results if not r.get("ok"))
    skip_n = sum(1 for r in results if r.get("skipped"))
    ch101_ok = all(r.get("ch101_unchanged", False) for r in results if r.get("ok"))

    report = {
        "inspected": len(inspected_list),
        "attempted": len(results),
        "success": ok_n,
        "failed": fail_n,
        "skipped_already_1s": skip_n,
        "ch101_all_unchanged_on_success": ch101_ok,
        "distributions_before": {
            "fps": dict(dist["fps"]),
            "GovLength": dict(dist["GovLength"]),
            "keyFrameInterval": dict(dist["keyFrameInterval"]),
            "codec": dict(dist["codec"]),
            "settings_by_fps": dist["settings_by_fps"],
        },
        "failures": [
            {"ip": r["ip"], "error": r.get("error"), "put_status": r.get("put_status"), "unexpected": r.get("unexpected_changes")}
            for r in results
            if not r.get("ok")
        ],
    }
    (ARTIFACT_DIR / "canary-apply-summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


async def cmd_ffprobe(limit_per_worker: int = 2) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = ARTIFACT_DIR / "canary-apply-results.json"
    sel_path = ARTIFACT_DIR / "canary-selection.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    selection = json.loads(sel_path.read_text(encoding="utf-8"))
    ok_ips = {r["ip"] for r in results if r.get("ok")} or {c["ip"] for c in selection["cameras"]}

    by_worker: dict[int, list[dict]] = defaultdict(list)
    for c in selection["cameras"]:
        if c["ip"] not in ok_ips:
            continue
        by_worker[int(c.get("workerId") or 0)].append(c)

    sample = []
    for w in sorted(by_worker.keys()):
        sample.extend(by_worker[w][:limit_per_worker])

    out = []
    for c in sample:
        cam = await camera_collection.find_one({"ip_address": c["ip"]})
        if not cam:
            out.append({"ip": c["ip"], "error": "missing"})
            continue
        probe = ffprobe_gop(c["ip"], cam.get("username") or "admin", str(cam.get("password") or ""))
        probe["workerId"] = c.get("workerId")
        probe["cameraUid"] = c.get("cameraUid")
        out.append(probe)
        print(json.dumps(probe), file=sys.stderr)

    (ARTIFACT_DIR / "canary-ffprobe.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["select", "apply", "ffprobe"])
    p.add_argument("--target", type=int, default=25)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--per-worker", type=int, default=2)
    args = p.parse_args()
    if args.mode == "select":
        await cmd_select(args.target)
    elif args.mode == "apply":
        await cmd_apply(args.concurrency)
    else:
        await cmd_ffprobe(args.per_worker)


if __name__ == "__main__":
    asyncio.run(main())
