"""Location-aware camera management summaries and stream checks."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection
from app.services.camera_locations import build_groups_hierarchy, camera_group_key_for_document
from app.services.camera_uid import make_camera_uid
from app.services.location_store import DEFAULT_SITE_NAME, list_buildings
from app.services.recording_schedule_store import recording_schedule
from app.services.stream_issues import ISSUE_LABELS, is_confirmed_offline, is_definitive_issue

logger = logging.getLogger(__name__)

_GO2RTC_CTX_CACHE: Dict[str, Any] = {
    "expires_at": 0.0,
    "stream_errors": {},
    "live_rows": {},
}
_GO2RTC_CTX_TTL = max(15, int(os.getenv("MGMT_GO2RTC_CTX_TTL_SECONDS", "60")))

_EMPTY_STATS = {
    "total": 0,
    "active": 0,
    "disabled": 0,
    "online": 0,
    "offline": 0,
    "errors": 0,
    "recording": 0,
    "liveConsumers": 0,
}


def _is_active(cam: dict) -> bool:
    return cam.get("is_active") is not False


def _camera_oid(camera_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(camera_id)
    except (InvalidId, TypeError):
        return None


def stream_online(cam: dict, live_row: dict) -> bool:
    """Proven online (health OK or active video)."""
    if cam.get("is_active") is False:
        return False
    return (live_row or {}).get("issueCategory") == "online"


def stream_confirmed_offline(cam: dict, live_row: dict) -> bool:
    """Alert-ready offline only (future mail/WhatsApp). Never checking/disabled."""
    if cam.get("is_active") is False:
        return False
    row = live_row or {}
    if "confirmedOffline" in row:
        return bool(row.get("confirmedOffline"))
    return is_confirmed_offline(row.get("issueCategory"))


def stream_playable(cam: dict, live_row: dict) -> bool:
    """Live View may try connect unless confirmed offline (alert-ready dead)."""
    if cam.get("is_active") is False:
        return False
    return not stream_confirmed_offline(cam, live_row)


def apply_stream_online_status(
    items: List[dict],
    live_rows: Dict[str, dict],
    *,
    playable_for_live: bool = False,
) -> None:
    """Attach liveStatus / confirmedOffline for UI and future alerts.

    Camera Management / Live badges use only Online vs Offline:
    - Online = not confirmed dead (includes not-yet-probed)
    - Offline + confirmedOffline / alertEligible = alert-ready failure
    Checking is internal to the health probe — not a user-facing state.
    """
    for item in items:
        cid = str(item.get("id") or item.get("_id") or "")
        row = live_rows.get(cid) or {}
        confirmed = stream_confirmed_offline(item, row)
        if playable_for_live:
            item["online"] = stream_playable(item, row)
        else:
            # Management: show Online until confirmed offline (no Checking bucket).
            item["online"] = (not confirmed) and item.get("is_active") is not False
        if confirmed:
            item["liveStatus"] = "offline"
        else:
            item["liveStatus"] = "online"
        item["confirmedOffline"] = confirmed
        # Explicit alias for a future notifier (email/WhatsApp) — no send logic yet.
        item["alertEligible"] = confirmed


def _stats_for_cameras(
    cameras: List[dict],
    *,
    stream_errors: Dict[str, str],
    recording_schedule_map: Dict[str, bool],
    live_rows: Dict[str, dict],
) -> Dict[str, Any]:
    stats = dict(_EMPTY_STATS)
    for cam in cameras:
        stats["total"] += 1
        cid = str(cam["_id"])
        active = _is_active(cam)
        if active:
            stats["active"] += 1
        else:
            stats["disabled"] += 1

        row = live_rows.get(cid) or {}
        if not active:
            pass
        elif stream_confirmed_offline(cam, row):
            stats["offline"] += 1
        else:
            # Not confirmed dead → Online (includes not-yet-probed).
            stats["online"] += 1

        uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "") or cid
        if stream_errors.get(uid) or stream_errors.get(cid):
            stats["errors"] += 1

        if recording_schedule_map.get(cid):
            stats["recording"] += 1

        stats["liveConsumers"] += int(row.get("subConsumers") or 0) + int(row.get("mainConsumers") or 0)

    return stats


def _group_cameras(cameras: List[dict]) -> Dict[str, List[dict]]:
    by_group: Dict[str, List[dict]] = {}
    for cam in cameras:
        group = camera_group_key_for_document(cam)
        if not group:
            continue
        by_group.setdefault(group, []).append(cam)
    return by_group


async def _load_go2rtc_context_from_health(
    cameras: Optional[List[dict]] = None,
) -> tuple[Dict[str, str], Dict[str, dict]]:
    """Fast path: cached MongoDB / in-memory stream health — no live go2rtc fan-out."""
    from app.services.stream_health import get_stream_health, hydrate_stream_health_from_db

    await hydrate_stream_health_from_db()

    stream_errors: Dict[str, str] = {}
    live_rows: Dict[str, dict] = {}

    def attach(cam: dict) -> None:
        cid = str(cam.get("_id") or cam.get("id") or "")
        if not cid:
            return
        uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "") or ""
        health = get_stream_health(cid, uid)
        if health and health.get("alarm"):
            cat = (health.get("category") or "offline").strip()
            msg = (health.get("message") or "").strip() or ISSUE_LABELS.get(cat, "Not streaming")
            if is_definitive_issue(cat, msg):
                live_rows[cid] = {
                    "cameraId": cid,
                    "cameraUid": uid,
                    "issueCategory": cat,
                    "confirmedOffline": True,
                    "issueMessage": msg,
                }
                stream_errors[cid] = msg
                if uid:
                    stream_errors[uid] = msg
                return
        live_rows[cid] = {
            "cameraId": cid,
            "cameraUid": uid,
            "issueCategory": "online",
            "confirmedOffline": False,
        }

    if cameras is not None:
        for cam in cameras:
            attach(cam)
    else:
        async for cam in camera_collection.find(
            {"is_active": {"$ne": False}},
            {"_id": 1, "camera_uid": 1, "ip_address": 1},
        ):
            attach(cam)

    return stream_errors, live_rows


def live_rows_from_memory_cache(cameras: List[dict]) -> Dict[str, dict]:
    """Immediate in-memory health only — never awaits hydrate / go2rtc / RTSP."""
    from app.services.stream_health import peek_stream_health

    live_rows: Dict[str, dict] = {}
    for cam in cameras:
        cid = str(cam.get("_id") or cam.get("id") or "")
        if not cid:
            continue
        uid = (
            cam.get("camera_uid")
            or cam.get("cameraUid")
            or make_camera_uid(cam.get("ip_address") or "")
            or ""
        )
        health = peek_stream_health(cid, uid)
        if health and health.get("alarm"):
            cat = (health.get("category") or "offline").strip()
            msg = (health.get("message") or "").strip() or ISSUE_LABELS.get(
                cat, "Not streaming"
            )
            if is_definitive_issue(cat, msg):
                live_rows[cid] = {
                    "cameraId": cid,
                    "cameraUid": uid,
                    "issueCategory": cat,
                    "confirmedOffline": True,
                    "issueMessage": msg,
                }
        # Timeout / 453 / missing row → playable/online. JPEG probes fail on busy OEM PTZs.
    return live_rows


async def _load_go2rtc_context(
    cameras: Optional[List[dict]] = None,
    *,
    force: bool = False,
) -> tuple[Dict[str, str], Dict[str, dict]]:
    """Map camera id → confirmed stream error; camera id → lightweight live row."""
    now = time.monotonic()
    scoped = cameras is not None
    if not scoped and not force and now < float(_GO2RTC_CTX_CACHE.get("expires_at") or 0):
        return _GO2RTC_CTX_CACHE["stream_errors"], _GO2RTC_CTX_CACHE["live_rows"]

    stream_errors, live_rows = await _load_go2rtc_context_from_health(cameras)
    if not scoped:
        _GO2RTC_CTX_CACHE["stream_errors"] = stream_errors
        _GO2RTC_CTX_CACHE["live_rows"] = live_rows
        _GO2RTC_CTX_CACHE["expires_at"] = now + _GO2RTC_CTX_TTL
    return stream_errors, live_rows


def invalidate_go2rtc_context_cache() -> None:
    _GO2RTC_CTX_CACHE["expires_at"] = 0.0


def enrich_hierarchy_with_stats(
    hierarchy: List[dict],
    cameras: List[dict],
    *,
    stream_errors: Dict[str, str],
    recording_schedule_map: Dict[str, bool],
    live_rows: Dict[str, dict],
) -> List[dict]:
    by_group = _group_cameras(cameras)
    building_stats: Dict[str, dict] = {}

    for building_entry in hierarchy:
        bname = building_entry.get("building") or ""
        b_stats = dict(_EMPTY_STATS)
        for fg in building_entry.get("floorGroups") or []:
            group = fg.get("camera_group") or ""
            group_cams = by_group.get(group, [])
            g_stats = _stats_for_cameras(
                group_cams,
                stream_errors=stream_errors,
                recording_schedule_map=recording_schedule_map,
                live_rows=live_rows,
            )
            fg["stats"] = g_stats
            fg["cameraCount"] = g_stats["total"]
            for key in _EMPTY_STATS:
                b_stats[key] += g_stats[key]
        building_entry["stats"] = b_stats
        building_stats[bname] = b_stats

    return hierarchy


def group_hierarchy_by_site(hierarchy: List[dict]) -> List[dict]:
    """Nest building → floor groups under site for management UI."""
    by_site: Dict[str, Dict[str, Any]] = {}
    for entry in hierarchy:
        site = (entry.get("site") or DEFAULT_SITE_NAME).strip()
        if site not in by_site:
            by_site[site] = {"site": site, "buildings": [], "stats": dict(_EMPTY_STATS)}
        by_site[site]["buildings"].append(
            {
                "site": site,
                "building": entry.get("building") or "",
                "floorGroups": entry.get("floorGroups") or [],
                "stats": entry.get("stats"),
            }
        )
        b_stats = entry.get("stats") or {}
        for key in _EMPTY_STATS:
            by_site[site]["stats"][key] += int(b_stats.get(key) or 0)
    return list(by_site.values())


async def _merge_configured_sites(sites: List[dict]) -> List[dict]:
    """Include every active site from Location Master even when it has no cameras yet."""
    try:
        from app.services.location_store import load_sites

        configured = await load_sites(include_inactive=False)
    except Exception:
        return sites
    by_name = {row["site"]: row for row in sites}
    for site_doc in configured:
        name = (site_doc.get("name") or "").strip()
        if not name or name in by_name:
            continue
        by_name[name] = {"site": name, "buildings": [], "stats": dict(_EMPTY_STATS)}
    return sorted(by_name.values(), key=lambda row: (row.get("site") or "").lower())


async def get_management_hierarchy(cameras: List[dict]) -> dict:
    location_buildings = await list_buildings()
    hierarchy = build_groups_hierarchy(cameras, location_buildings, cameras_only=False)
    stream_errors, live_rows = await _load_go2rtc_context(cameras)
    schedule = dict(recording_schedule)
    hierarchy = enrich_hierarchy_with_stats(
        hierarchy,
        cameras,
        stream_errors=stream_errors,
        recording_schedule_map=schedule,
        live_rows=live_rows,
    )
    totals = _stats_for_cameras(
        cameras,
        stream_errors=stream_errors,
        recording_schedule_map=schedule,
        live_rows=live_rows,
    )
    return {
        "sites": await _merge_configured_sites(group_hierarchy_by_site(hierarchy)),
        "buildings": hierarchy,
        "totals": totals,
        "streamErrors": stream_errors,
    }


async def test_camera_stream(camera_id: str) -> dict:
    """Quick RTSP connectivity check for management UI."""
    oid = _camera_oid(camera_id)
    if not oid:
        return {"ok": False, "error": "Invalid camera id"}
    cam = await camera_collection.find_one({"_id": oid})
    if not cam:
        return {"ok": False, "error": "Camera not found"}

    from app.services.stream_health import record_stream_health

    def finish(ok: bool, message: str, *, category: str | None = None) -> dict:
        record_stream_health(cam, ok=ok, message=message, category=category)
        return {"ok": True, "message": message} if ok else {"ok": False, "error": message}

    urls = build_camera_rtsp_urls(cam)
    sub_url = urls.get("sub_rtsp_url") or urls.get("main_rtsp_url") or ""
    if not sub_url:
        return finish(False, "No RTSP URL configured", category="missing_url")

    try:
        from app.services.ffmpeg_util import ffmpeg_bin

        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            sub_url,
            "-t",
            "2",
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return finish(True, "Stream OK")
        if re.search(r"401|unauthorized|wrong user|password", err, re.I):
            return finish(
                False,
                "Authentication failed (wrong username/password)",
                category="wrong_password",
            )
        return finish(False, err[:500] or f"ffmpeg exit {proc.returncode}")
    except asyncio.TimeoutError:
        return finish(False, "Stream test timed out", category="timeout")
    except Exception as exc:
        return finish(False, str(exc))


async def reload_go2rtc_for_group(camera_group: str) -> dict:
    from app.services.go2rtc_service import ensure_go2rtc_streams

    result = await ensure_go2rtc_streams()
    return {
        "ok": bool(result.get("ok")),
        "camera_group": camera_group,
        "message": "go2rtc reloaded",
        "detail": result,
    }
