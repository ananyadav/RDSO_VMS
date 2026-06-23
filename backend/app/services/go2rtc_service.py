"""
go2rtc relay — default live view engine (HLS V1 remains env fallback).

Grid:   {camera_uid}_sub  → RTSP channel 102
Fullscreen: {camera_uid}_main → RTSP channel 101

go2rtc opens RTSP only when a browser consumer connects (lazy per stream).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import yaml

from app.core.database import camera_collection
from app.services.camera_uid import make_camera_uid
from app.services.rtsp_utils import build_camera_rtsp_urls, mask_rtsp_url

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
GO2RTC_DIR = Path(os.getenv("GO2RTC_DIR", str(_PROJECT_ROOT / "go2rtc"))).resolve()
RUNTIME_DIR = GO2RTC_DIR / "runtime"
CONFIG_PATH = RUNTIME_DIR / "go2rtc.yaml"

GO2RTC_ENABLED = os.getenv("GO2RTC_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
LIVE_PROVIDER = os.getenv("LIVE_PROVIDER", "go2rtc").strip().lower()
GO2RTC_API_HOST = os.getenv("GO2RTC_API_HOST", "127.0.0.1").strip()
GO2RTC_API_PORT = int(os.getenv("GO2RTC_API_PORT", "1984"))
GO2RTC_API_URL = f"http://{GO2RTC_API_HOST}:{GO2RTC_API_PORT}"

_proc: Optional[asyncio.subprocess.Process] = None
_consumer_counts: Dict[str, int] = {}


def go2rtc_bin() -> Path:
    raw = (os.getenv("GO2RTC_BIN") or "").strip()
    if raw:
        return Path(raw).resolve()
    if os.name == "nt":
        return GO2RTC_DIR / "bin" / "go2rtc.exe"
    return GO2RTC_DIR / "bin" / "go2rtc"


def stream_name(camera_id: str, profile: str) -> str:
    """profile: sub (102 grid) | main (101 fullscreen)."""
    p = profile.strip().lower()
    if p not in ("sub", "main"):
        p = "sub"
    return f"{camera_id}_{p}"


def local_recording_rtsp_url(camera_uid: str, stream: str = "main") -> str:
    """RTSP URL via go2rtc relay — avoids extra direct connections to the camera."""
    host = os.getenv("GO2RTC_RTSP_HOST", "127.0.0.1").strip()
    port = os.getenv("GO2RTC_RTSP_PORT", "8554").strip()
    profile = "main" if (stream or "main").strip().lower() == "main" else "sub"
    return f"rtsp://{host}:{port}/{stream_name(camera_uid, profile)}"


def _rtsp_with_tcp(url: str) -> str:
    if not url:
        return url
    if "#" in url:
        return url if "rtsp_transport" in url else f"{url}#rtsp_transport=tcp"
    return f"{url}#rtsp_transport=tcp"


async def _all_cameras() -> List[dict]:
    query = {
        "$or": [
            {"is_active": True},
            {"is_active": {"$exists": False}},
        ]
    }
    cursor = camera_collection.find(query).sort("name", 1)
    return await cursor.to_list(length=500)


async def build_all_streams_config() -> Dict[str, Any]:
    """Build go2rtc stream map for every camera in MongoDB."""
    cameras = await _all_cameras()
    streams: Dict[str, str] = {}
    masked: Dict[str, str] = {}
    cameras_meta: List[dict] = []
    errors: List[str] = []

    for cam in cameras:
        camera_id = str(cam["_id"])
        camera_uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "") or camera_id
        name = cam.get("name") or camera_id
        urls = build_camera_rtsp_urls(cam)
        sub_url = _rtsp_with_tcp(urls.get("sub_rtsp_url") or "")
        main_url = _rtsp_with_tcp(urls.get("main_rtsp_url") or "")

        sub_key = stream_name(camera_uid, "sub")
        main_key = stream_name(camera_uid, "main")

        if sub_url:
            streams[sub_key] = sub_url
            masked[sub_key] = mask_rtsp_url(sub_url)
        else:
            errors.append(f"{name}: missing sub/102 URL")

        if main_url:
            streams[main_key] = main_url
            masked[main_key] = mask_rtsp_url(main_url)
        else:
            errors.append(f"{name}: missing main/101 URL")

        cameras_meta.append(
            {
                "cameraId": camera_id,
                "cameraUid": camera_uid,
                "cameraName": name,
                "site": (cam.get("site") or "").strip(),
                "building": (cam.get("building") or "").strip(),
                "floor": (cam.get("floor_group") or cam.get("floor") or "").strip(),
                "camera_group": (cam.get("camera_group") or "").strip(),
                "subStream": sub_key,
                "mainStream": main_key,
                "hasSub": bool(sub_url),
                "hasMain": bool(main_url),
            }
        )

    if not streams:
        return {
            "ok": False,
            "error": "No camera streams configured",
            "streams": {},
            "cameras": [],
            "errors": errors,
        }

    return {
        "ok": True,
        "streamCount": len(streams),
        "cameraCount": len(cameras_meta),
        "streams": streams,
        "masked": masked,
        "cameras": cameras_meta,
        "errors": errors,
    }


def _base_yaml(streams: Dict[str, str]) -> dict:
    return {
        "api": {"listen": f"{GO2RTC_API_HOST}:{GO2RTC_API_PORT}"},
        "rtsp": {"listen": "127.0.0.1:8554"},
        "webrtc": {
            "listen": ":8555",
            "candidates": [f"{GO2RTC_API_HOST}:8555", "stun:8555"],
            "ice_servers": [
                {
                    "urls": [
                        "stun:stun.cloudflare.com:3478",
                        "stun:stun.l.google.com:19302",
                    ]
                }
            ],
        },
        "streams": streams,
        "log": {"level": os.getenv("GO2RTC_LOG_LEVEL", "info")},
    }


async def write_config_file() -> Dict[str, Any]:
    built = await build_all_streams_config()
    if not built.get("ok"):
        return built

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.safe_dump(_base_yaml(built["streams"]), sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "[go2rtc] Wrote config %s cameras=%s streams=%s",
        CONFIG_PATH,
        built.get("cameraCount"),
        built.get("streamCount"),
    )
    return built


async def is_api_healthy() -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{GO2RTC_API_URL}/api") as resp:
                return resp.status == 200
    except Exception:
        return False


async def fetch_go2rtc_streams() -> Dict[str, Any]:
    """GET go2rtc /api/streams — producers/consumers per stream."""
    try:
        timeout = aiohttp.ClientTimeout(total=3.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{GO2RTC_API_URL}/api/streams") as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except Exception as exc:
        logger.debug("[go2rtc] streams API: %s", exc)
        return {}


async def sync_streams_to_go2rtc(streams: Dict[str, str]) -> Dict[str, Any]:
    """
    Push MongoDB stream map into a running go2rtc instance.

    Needed when go2rtc was started with an old pilot config (e.g. Cam18_sub)
    while the UI requests {cameraObjectId}_sub.
    """
    if not streams:
        return {"ok": False, "error": "no streams", "added": 0, "updated": 0, "removed": 0}

    timeout = aiohttp.ClientTimeout(total=60.0)
    added = 0
    updated = 0
    removed = 0
    errors: List[str] = []

    existing = await fetch_go2rtc_streams()
    existing_names = set(existing.keys()) if isinstance(existing, dict) else set()
    desired = set(streams.keys())

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, src in streams.items():
            try:
                async with session.put(
                    f"{GO2RTC_API_URL}/api/streams",
                    params={"name": name, "src": src},
                ) as resp:
                    if resp.status in (200, 201):
                        if name in existing_names:
                            updated += 1
                        else:
                            added += 1
                    else:
                        body = await resp.text()
                        errors.append(f"{name}: HTTP {resp.status} {body[:160]}")
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        for name in sorted(existing_names - desired):
            try:
                async with session.delete(
                    f"{GO2RTC_API_URL}/api/streams",
                    params={"src": name},
                ) as resp:
                    if resp.status in (200, 204):
                        removed += 1
                    else:
                        body = await resp.text()
                        errors.append(f"delete {name}: HTTP {resp.status} {body[:120]}")
            except Exception as exc:
                errors.append(f"delete {name}: {exc}")

    if added or updated or removed:
        logger.info(
            "[go2rtc] Stream sync added=%s updated=%s removed=%s",
            added,
            updated,
            removed,
        )
    if errors:
        logger.warning("[go2rtc] Stream sync errors: %s", errors[:5])

    return {
        "ok": len(errors) == 0,
        "added": added,
        "updated": updated,
        "removed": removed,
        "errors": errors,
    }


async def _restart_go2rtc_api() -> bool:
    """Ask running go2rtc to restart (drops active viewers)."""
    try:
        timeout = aiohttp.ClientTimeout(total=15.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{GO2RTC_API_URL}/api/restart") as resp:
                if resp.status != 200:
                    return False
        for _ in range(40):
            await asyncio.sleep(0.25)
            if await is_api_healthy():
                return True
    except Exception as exc:
        logger.debug("[go2rtc] restart API failed: %s", exc)
    return False


def _running_result(built: Dict[str, Any], *, reused: bool, sync: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "running": True,
        "reused": reused,
        "apiUrl": GO2RTC_API_URL,
        "streamCount": built.get("streamCount"),
        "cameraCount": built.get("cameraCount"),
        "configPath": str(CONFIG_PATH),
        "sync": sync,
        "pid": _proc.pid if _proc and _proc.returncode is None else None,
    }


def report_consumer(stream: str, delta: int) -> None:
    """Frontend-reported active player count (diagnostics)."""
    prev = _consumer_counts.get(stream, 0)
    _consumer_counts[stream] = max(0, prev + delta)
    if _consumer_counts[stream] == 0:
        _consumer_counts.pop(stream, None)


async def start_go2rtc(*, reload: bool = False) -> Dict[str, Any]:
    global _proc

    if not GO2RTC_ENABLED:
        return {"ok": False, "error": "GO2RTC_ENABLED=false", "running": False}

    built = await write_config_file()
    if not built.get("ok"):
        return {**built, "running": False}

    streams: Dict[str, str] = built.get("streams") or {}

    if reload:
        await stop_go2rtc()
        if await is_api_healthy():
            await _restart_go2rtc_api()
        if await is_api_healthy():
            sync = await sync_streams_to_go2rtc(streams)
            return _running_result(built, reused=False, sync=sync)

    if await is_api_healthy():
        sync = await sync_streams_to_go2rtc(streams)
        return _running_result(built, reused=True, sync=sync)

    binary = go2rtc_bin()
    if not binary.is_file():
        return {
            "ok": False,
            "running": False,
            "error": f"go2rtc binary not found: {binary}",
            "hint": "Download from https://github.com/AlexxIT/go2rtc/releases into go2rtc/bin/",
        }

    if _proc and _proc.returncode is None:
        sync = await sync_streams_to_go2rtc(streams)
        return _running_result(built, reused=True, sync=sync)

    try:
        _proc = await asyncio.create_subprocess_exec(
            str(binary),
            "-config",
            str(CONFIG_PATH),
            cwd=str(GO2RTC_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        logger.error("[go2rtc] Failed to start: %s", exc)
        return {"ok": False, "running": False, "error": str(exc)}

    for _ in range(40):
        await asyncio.sleep(0.25)
        if await is_api_healthy():
            sync = await sync_streams_to_go2rtc(streams)
            logger.info(
                "[go2rtc] Started pid=%s streams=%s sync_added=%s",
                _proc.pid,
                built.get("streamCount"),
                sync.get("added"),
            )
            return _running_result(built, reused=False, sync=sync)
        if _proc.returncode is not None:
            err = ""
            if _proc.stderr:
                raw = await _proc.stderr.read()
                err = raw.decode("utf-8", errors="ignore")[:500]
            return {
                "ok": False,
                "running": False,
                "error": f"go2rtc exited rc={_proc.returncode}",
                "detail": err,
            }

    return {
        "ok": False,
        "running": False,
        "error": "go2rtc API did not become ready within 10s",
        "pid": _proc.pid if _proc else None,
    }


async def stop_go2rtc() -> None:
    global _proc
    proc = _proc
    _proc = None
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("[go2rtc] Stopped pid=%s", proc.pid)


async def get_go2rtc_status() -> Dict[str, Any]:
    built = await build_all_streams_config()
    healthy = await is_api_healthy()
    return {
        "enabled": GO2RTC_ENABLED,
        "running": healthy,
        "liveProvider": LIVE_PROVIDER,
        "apiUrl": GO2RTC_API_URL,
        "proxyBase": "/go2rtc",
        "binary": str(go2rtc_bin()),
        "binaryFound": go2rtc_bin().is_file(),
        "configPath": str(CONFIG_PATH),
        "streamCount": built.get("streamCount", 0) if built.get("ok") else 0,
        "cameraCount": built.get("cameraCount", 0) if built.get("ok") else 0,
        "pid": _proc.pid if _proc and _proc.returncode is None else None,
        "errors": built.get("errors", []) if built.get("ok") else [built.get("error")],
    }


async def get_go2rtc_diagnostics() -> Dict[str, Any]:
    """Full status for debug UI: streams, online/offline, consumers."""
    from app.services.stream_issues import (
        ISSUE_LABELS,
        producer_error_text,
        stream_issue_from_row,
        summarize_issues,
    )

    built = await build_all_streams_config()
    status = await get_go2rtc_status()
    api_streams = await fetch_go2rtc_streams()
    configured_names = {
        s
        for cam in (built.get("cameras") or [])
        for s in (cam["subStream"], cam["mainStream"])
    }
    api_names = set(api_streams.keys()) if isinstance(api_streams, dict) else set()
    missing_in_go2rtc = sorted(configured_names - api_names)
    stale_in_go2rtc = sorted(api_names - configured_names)

    config_errors_by_name: Dict[str, str] = {}
    for err in built.get("errors") or []:
        if ":" in err:
            name, msg = err.split(":", 1)
            config_errors_by_name[name.strip()] = msg.strip()

    rows: List[dict] = []
    online_count = 0
    offline_count = 0
    active_consumers = 0

    for cam in built.get("cameras") or []:
        sub = cam["subStream"]
        main = cam["mainStream"]
        sub_info = api_streams.get(sub) or {}
        main_info = api_streams.get(main) or {}

        sub_producers = sub_info.get("producers") or []
        main_producers = main_info.get("producers") or []
        sub_consumers = len(sub_info.get("consumers") or [])
        main_consumers = len(main_info.get("consumers") or [])
        ui_sub = _consumer_counts.get(sub, 0)
        ui_main = _consumer_counts.get(main, 0)

        sub_online = len(sub_producers) > 0
        main_online = len(main_producers) > 0
        if sub_online or main_online:
            online_count += 1
        else:
            offline_count += 1

        active_consumers += sub_consumers + main_consumers

        sub_stale = max(0, sub_consumers - ui_sub)
        main_stale = max(0, main_consumers - ui_main)

        cam_name = cam["cameraName"]
        cfg_err = config_errors_by_name.get(cam_name)
        issue_cat, issue_msg = stream_issue_from_row(
            sub_online=sub_online,
            main_online=main_online,
            sub_producers=sub_producers,
            main_producers=main_producers,
            config_error=cfg_err,
        )

        rows.append(
            {
                "cameraId": cam["cameraId"],
                "cameraName": cam_name,
                "site": cam.get("site") or "",
                "building": cam.get("building") or "",
                "floor": cam.get("floor") or "",
                "camera_group": cam.get("camera_group") or "",
                "subStream": sub,
                "mainStream": main,
                "subOnline": sub_online,
                "mainOnline": main_online,
                "subConsumers": sub_consumers,
                "mainConsumers": main_consumers,
                "uiSubConsumers": ui_sub,
                "uiMainConsumers": ui_main,
                "subStaleConsumers": sub_stale,
                "mainStaleConsumers": main_stale,
                "subOrphaned": sub_stale > 0,
                "mainOrphaned": main_stale > 0,
                "issueCategory": issue_cat,
                "issueLabel": ISSUE_LABELS.get(issue_cat, issue_cat),
                "issueMessage": issue_msg or producer_error_text(sub_producers) or producer_error_text(main_producers),
            }
        )

    return {
        **status,
        "configuredStreamCount": built.get("streamCount", 0),
        "camerasOnline": online_count,
        "camerasOffline": offline_count,
        "activeConsumers": active_consumers,
        "uiTrackedConsumers": sum(_consumer_counts.values()),
        "streams": rows,
        "configErrors": built.get("errors") or [],
        "missingInGo2rtc": missing_in_go2rtc,
        "staleInGo2rtc": stale_in_go2rtc,
        "issueSummary": summarize_issues(rows),
        "issueLabels": ISSUE_LABELS,
        "locations": _build_location_tree(rows),
    }


def _build_location_tree(rows: List[dict]) -> List[dict]:
    sites: Dict[str, dict] = {}
    for row in rows:
        site = (row.get("site") or "").strip() or "Unassigned"
        building = (row.get("building") or "").strip() or "Unassigned"
        floor = (row.get("floor") or "").strip() or "Unassigned"

        if site not in sites:
            sites[site] = {"site": site, "buildings": {}}
        srow = sites[site]
        if building not in srow["buildings"]:
            srow["buildings"][building] = {"building": building, "floors": {}}
        brow = srow["buildings"][building]
        if floor not in brow["floors"]:
            brow["floors"][floor] = {"floor": floor, "cameraCount": 0}
        brow["floors"][floor]["cameraCount"] += 1

    out = []
    for site_name in sorted(sites.keys()):
        srow = sites[site_name]
        buildings = []
        for bname in sorted(srow["buildings"].keys()):
            brow = srow["buildings"][bname]
            floors = [brow["floors"][f] for f in sorted(brow["floors"].keys())]
            buildings.append({"building": bname, "floors": floors})
        out.append({"site": site_name, "buildings": buildings})
    return out


async def ensure_go2rtc_streams() -> Dict[str, Any]:
    """Regenerate config from MongoDB and push streams to go2rtc (no full restart)."""
    if not GO2RTC_ENABLED:
        return {"ok": False, "error": "GO2RTC_ENABLED=false", "running": False}

    built = await write_config_file()
    if not built.get("ok"):
        return {**built, "running": False}

    streams: Dict[str, str] = built.get("streams") or {}
    if await is_api_healthy():
        sync = await sync_streams_to_go2rtc(streams)
        return _running_result(built, reused=True, sync=sync)

    return await start_go2rtc()


async def start_go2rtc_on_startup() -> None:
    if not GO2RTC_ENABLED:
        logger.info("[go2rtc] Disabled (GO2RTC_ENABLED=false)")
        return
    if LIVE_PROVIDER != "go2rtc":
        logger.info("[go2rtc] LIVE_PROVIDER=%s — go2rtc not auto-started", LIVE_PROVIDER)
        return
    result = await start_go2rtc()
    if result.get("ok"):
        logger.info(
            "[go2rtc] Ready api=%s cameras=%s streams=%s",
            GO2RTC_API_URL,
            result.get("cameraCount"),
            result.get("streamCount"),
        )
    else:
        logger.warning("[go2rtc] Not started: %s", result.get("error"))


def get_live_config() -> Dict[str, Any]:
    return {
        "provider": LIVE_PROVIDER,
        "hlsFallback": os.getenv("HLS_FALLBACK_ENABLED", "true").strip().lower()
        in ("1", "true", "yes"),
        "go2rtcEnabled": GO2RTC_ENABLED,
    }
