"""Location-aware camera management summaries and stream checks."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import camera_collection
from app.services.camera_locations import build_groups_hierarchy, camera_group_key_for_document
from app.services.camera_uid import make_camera_uid
from app.services.location_store import DEFAULT_SITE_NAME, list_buildings
from app.services.recording_schedule_store import recording_schedule
from app.services.rtsp_utils import build_camera_rtsp_urls

logger = logging.getLogger(__name__)

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
    """Online when active and go2rtc reports sub or main stream up."""
    if cam.get("is_active") is False:
        return False
    cid = str(cam.get("_id") or cam.get("id") or "")
    row = live_row or {}
    return bool(row.get("subOnline") or row.get("mainOnline"))


def apply_stream_online_status(items: List[dict], live_rows: Dict[str, dict]) -> None:
    for item in items:
        cid = str(item.get("id") or item.get("_id") or "")
        row = live_rows.get(cid) or {}
        item["online"] = stream_online(item, row)


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
        online = stream_online(cam, row) if active else False
        if online:
            stats["online"] += 1
        elif active:
            stats["offline"] += 1

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


async def _load_go2rtc_context() -> tuple[Dict[str, str], Dict[str, dict]]:
    """Map camera_uid/mongo id → stream error; mongo id → go2rtc row."""
    stream_errors: Dict[str, str] = {}
    live_rows: Dict[str, dict] = {}
    try:
        from app.services.go2rtc_service import get_go2rtc_diagnostics

        diag = await get_go2rtc_diagnostics()
        for row in diag.get("streams") or []:
            cid = row.get("cameraId") or ""
            live_rows[cid] = row
            if not row.get("subOnline") and not row.get("mainOnline"):
                stream_errors[cid] = "Stream offline or auth failed"
    except Exception as exc:
        logger.debug("[MGMT] go2rtc diagnostics unavailable: %s", exc)
    return stream_errors, live_rows


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


async def get_management_hierarchy(cameras: List[dict]) -> dict:
    location_buildings = await list_buildings()
    hierarchy = build_groups_hierarchy(cameras, location_buildings, cameras_only=True)
    stream_errors, live_rows = await _load_go2rtc_context()
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
        "sites": group_hierarchy_by_site(hierarchy),
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

    urls = build_camera_rtsp_urls(cam)
    sub_url = urls.get("sub_rtsp_url") or urls.get("main_rtsp_url") or ""
    if not sub_url:
        return {"ok": False, "error": "No RTSP URL configured"}

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
            return {"ok": True, "message": "Stream OK"}
        if re.search(r"401|unauthorized|wrong user|password", err, re.I):
            return {"ok": False, "error": "Authentication failed (wrong username/password)"}
        return {"ok": False, "error": err[:500] or f"ffmpeg exit {proc.returncode}"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Stream test timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def reload_go2rtc_for_group(camera_group: str) -> dict:
    from app.services.go2rtc_service import ensure_go2rtc_streams

    result = await ensure_go2rtc_streams()
    return {
        "ok": bool(result.get("ok")),
        "camera_group": camera_group,
        "message": "go2rtc reloaded",
        "detail": result,
    }
