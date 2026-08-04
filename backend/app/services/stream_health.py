"""Cached, bounded real-stream probes for go2rtc diagnostics."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp
from bson import ObjectId

from app.core.database import camera_collection
from app.services.camera_uid import make_camera_uid
from app.services.go2rtc_workers import get_api_url_for_camera_doc
from app.services.stream_issues import classify_stream_error

logger = logging.getLogger(__name__)

PROBE_CONCURRENCY = max(1, int(os.getenv("STREAM_HEALTH_CONCURRENCY", "3")))
PROBE_PER_WORKER = max(1, int(os.getenv("STREAM_HEALTH_PER_WORKER", "1")))
PROBE_TIMEOUT_SECONDS = max(3, int(os.getenv("STREAM_HEALTH_TIMEOUT_SECONDS", "20")))
CACHE_TTL_SECONDS = max(30, int(os.getenv("STREAM_HEALTH_CACHE_TTL_SECONDS", "900")))
# Transient categories need this many consecutive failures before Errors UI alarms.
CONFIRM_STRIKES = max(1, int(os.getenv("STREAM_HEALTH_CONFIRM_STRIKES", "3")))
FAILURE_MAX_AGE_HOURS = max(1, int(os.getenv("STREAM_HEALTH_FAILURE_MAX_AGE_HOURS", "6")))
# Auth / missing URL / codec / dead network are usually definitive on first sighting.
DEFINITIVE_CATEGORIES = frozenset({"wrong_password", "missing_url", "codec"})
# These oscillate under load; count them as one transient bucket for strikes.
TRANSIENT_CATEGORIES = frozenset({"timeout", "offline", "other"})


def _is_definitive_failure(category: str, message: str = "") -> bool:
    if category in DEFINITIVE_CATEGORIES:
        return True
    m = (message or "").lower()
    # Power cut / unreachable host — treat as confirmed offline immediately.
    if re.search(r"rtsp port \d+ closed|port closed or unreachable|destination host unreachable|no route to host", m):
        return True
    return False

_results: Dict[str, dict] = {}
_scan_task: Optional[asyncio.Task] = None
_hydrate_task: Optional[asyncio.Task] = None
_hydrate_lock: Optional[asyncio.Lock] = None
_hydrated = False
_scan_state: Dict[str, Any] = {
    "running": False,
    "total": 0,
    "completed": 0,
    "healthy": 0,
    "errors": 0,
    "suspects": 0,
    "startedAt": None,
    "completedAt": None,
    "error": None,
}


def _get_hydrate_lock() -> asyncio.Lock:
    global _hydrate_lock
    if _hydrate_lock is None:
        _hydrate_lock = asyncio.Lock()
    return _hydrate_lock


def _strike_bucket(category: str) -> str:
    cat = category or "offline"
    if cat in TRANSIENT_CATEGORIES:
        return "transient"
    return cat


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _camera_id(camera: dict) -> str:
    return str(camera.get("_id") or camera.get("id") or "")


def _store_result(result: dict) -> None:
    cid = str(result.get("cameraId") or "")
    uid = str(result.get("cameraUid") or "")
    if cid:
        _results[cid] = result
    if uid:
        _results[uid] = result


def _parse_checked_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _classify_frame_response(status: int, body: bytes) -> tuple[bool, str, str]:
    if status == 200 and len(body) > 1000:
        return True, "online", ""
    text = body.decode("utf-8", errors="replace").strip()[:500]
    if text:
        return False, classify_stream_error(text), text
    if status == 404:
        return False, "missing_url", "Stream not registered in go2rtc"
    if status >= 500:
        return False, "other", f"go2rtc frame probe failed (HTTP {status})"
    return False, "offline", "No video frame received from camera"


async def _rtsp_port_open(ip: str, port: int) -> bool:
    if not ip:
        return False
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=3)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def finalize_probe_result(
    raw: dict,
    *,
    previous: Optional[dict] = None,
    force_alarm: bool = False,
) -> dict:
    """Apply confirmation policy so one flaky timeout does not become an alarm."""
    result = dict(raw)
    if result.get("ok"):
        result.update({"alarm": False, "strikes": 0, "suspect": False})
        return result

    category = result.get("category") or "offline"
    message = str(result.get("message") or "")
    needed = 1 if (force_alarm or _is_definitive_failure(category, message)) else CONFIRM_STRIKES
    prev_strikes = 0
    if previous and not previous.get("ok"):
        prev_cat = previous.get("category") or "offline"
        if _strike_bucket(prev_cat) == _strike_bucket(category):
            prev_strikes = int(previous.get("strikes") or 0)
    strikes = prev_strikes + 1
    alarm = strikes >= needed
    result.update(
        {
            "alarm": alarm,
            "strikes": strikes,
            "suspect": not alarm,
        }
    )
    return result


async def _persist_health(camera: dict, result: dict) -> None:
    """Persist confirmed outcomes only — skip first-strike suspects to cut write noise."""
    cid = result.get("cameraId") or _camera_id(camera)
    if not cid or not ObjectId.is_valid(cid):
        return
    if not result.get("ok") and not result.get("alarm"):
        return
    try:
        await camera_collection.update_one(
            {"_id": ObjectId(cid)},
            {
                "$set": {
                    "stream_health_ok": bool(result.get("ok")),
                    "stream_health_alarm": bool(result.get("alarm")),
                    "stream_health_strikes": int(result.get("strikes") or 0),
                    "stream_health_category": result.get("category") or "offline",
                    "stream_health_message": result.get("message") or "",
                    "stream_health_checked_at": result.get("checkedAt") or _iso(),
                }
            },
        )
    except Exception as exc:
        logger.debug("[stream-health] persist failed for %s: %s", cid, exc)


def _result_from_camera_doc(camera: dict) -> Optional[dict]:
    checked = camera.get("stream_health_checked_at")
    if not checked:
        return None
    checked_dt = _parse_checked_at(checked)
    ok = bool(camera.get("stream_health_ok"))
    alarm = bool(camera.get("stream_health_alarm"))
    # Never restore old non-alarm / expired failures into the Errors UI.
    if not ok:
        if not alarm:
            return None
        if checked_dt is None:
            return None
        age_h = (_now() - checked_dt).total_seconds() / 3600.0
        if age_h > FAILURE_MAX_AGE_HOURS:
            return None

    cid = _camera_id(camera)
    ip = (camera.get("ip_address") or "").strip()
    uid = camera.get("camera_uid") or make_camera_uid(ip) or cid
    return {
        "cameraId": cid,
        "cameraUid": uid,
        "ok": ok,
        "alarm": False if ok else alarm,
        "strikes": int(camera.get("stream_health_strikes") or (0 if ok else CONFIRM_STRIKES)),
        "suspect": False,
        "category": "online" if ok else (camera.get("stream_health_category") or "offline"),
        "message": "" if ok else (camera.get("stream_health_message") or "Stream probe failed"),
        "checkedAt": checked if isinstance(checked, str) else _iso(checked_dt),
        "persisted": True,
    }


async def hydrate_stream_health_from_db() -> int:
    """Load last known *confirmed* probe results so real Errors survive restarts."""
    global _hydrated
    if _hydrated:
        return len({str(v.get("cameraId")) for v in _results.values() if v.get("cameraId")})

    async with _get_hydrate_lock():
        if _hydrated:
            return len({str(v.get("cameraId")) for v in _results.values() if v.get("cameraId")})

        loaded = 0
        try:
            cameras = await camera_collection.find(
                {
                    "is_active": {"$ne": False},
                    "stream_health_checked_at": {"$exists": True, "$ne": None},
                },
                {
                    "_id": 1,
                    "camera_uid": 1,
                    "ip_address": 1,
                    "stream_health_ok": 1,
                    "stream_health_alarm": 1,
                    "stream_health_strikes": 1,
                    "stream_health_category": 1,
                    "stream_health_message": 1,
                    "stream_health_checked_at": 1,
                },
            ).to_list(None)
            for camera in cameras:
                result = _result_from_camera_doc(camera)
                if not result:
                    continue
                existing = _results.get(result["cameraId"]) or (
                    result.get("cameraUid") and _results.get(result["cameraUid"])
                )
                if existing and not existing.get("persisted"):
                    continue
                _store_result(result)
                loaded += 1
            if loaded:
                unique = {str(v.get("cameraId")): v for v in _results.values() if v.get("cameraId")}
                failed = sum(1 for v in unique.values() if v.get("alarm"))
                healthy = sum(1 for v in unique.values() if v.get("ok"))
                if not _scan_state.get("running"):
                    _scan_state["cachedFromDb"] = loaded
                    _scan_state["errors"] = max(int(_scan_state.get("errors") or 0), failed)
                    _scan_state["healthy"] = max(int(_scan_state.get("healthy") or 0), healthy)
                logger.info("[stream-health] Hydrated %s confirmed probe result(s) from MongoDB", loaded)
            _hydrated = True
        except Exception as exc:
            logger.warning("[stream-health] DB hydrate failed: %s", exc)
            _hydrated = False
        return loaded


def ensure_stream_health_hydrated() -> None:
    """Kick off DB hydrate once without blocking callers."""
    global _hydrate_task
    if _hydrated:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _hydrate_task and not _hydrate_task.done():
        return
    _hydrate_task = loop.create_task(hydrate_stream_health_from_db())


def clear_stream_health_alarm(camera_id: str, camera_uid: str = "") -> Optional[dict]:
    """Mark a camera healthy in-cache after live producers prove it is OK."""
    previous = get_stream_health(camera_id, camera_uid)
    if not previous or previous.get("ok"):
        return previous
    result = {
        "cameraId": camera_id or previous.get("cameraId") or "",
        "cameraUid": camera_uid or previous.get("cameraUid") or "",
        "ok": True,
        "alarm": False,
        "strikes": 0,
        "suspect": False,
        "category": "online",
        "message": "",
        "checkedAt": _iso(),
        "clearedByLiveProducer": True,
    }
    _store_result(result)
    if ObjectId.is_valid(result["cameraId"]):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_persist_health({"_id": result["cameraId"]}, result))
        except RuntimeError:
            pass
    return result


async def _probe_camera(session: aiohttp.ClientSession, camera: dict) -> dict:
    cid = _camera_id(camera)
    ip = (camera.get("ip_address") or "").strip()
    uid = camera.get("camera_uid") or make_camera_uid(ip) or cid
    checked_at = _iso()
    if not uid:
        raw = {
            "cameraId": cid,
            "cameraUid": "",
            "ok": False,
            "category": "missing_url",
            "message": "Camera has no stream identity",
            "checkedAt": checked_at,
        }
        return finalize_probe_result(raw, previous=get_stream_health(cid, ""), force_alarm=True)

    try:
        rtsp_port = int(camera.get("port") or 554)
    except (TypeError, ValueError):
        rtsp_port = 554

    try:
        base_url = await get_api_url_for_camera_doc(camera)
        async with session.get(
            f"{base_url.rstrip('/')}/api/frame.jpeg",
            params={"src": f"{uid}_sub", "timeout": str(PROBE_TIMEOUT_SECONDS - 2)},
            timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_SECONDS),
        ) as response:
            body = await response.read()
            ok, category, message = _classify_frame_response(response.status, body)
    except asyncio.TimeoutError:
        ok, category, message = False, "timeout", "Stream probe timed out"
    except aiohttp.ClientConnectorError as exc:
        ok, category, message = False, "timeout", f"go2rtc worker unreachable: {exc.os_error or exc}"
    except Exception as exc:
        ok, category, message = False, classify_stream_error(str(exc)), str(exc)[:500]

    # TCP is only a clarifying hint after a failed frame probe.
    if not ok and category == "timeout" and not await _rtsp_port_open(ip, rtsp_port):
        message = f"RTSP port {rtsp_port} closed or unreachable"
        category = "timeout"

    raw = {
        "cameraId": cid,
        "cameraUid": uid,
        "ok": ok,
        "category": category,
        "message": message,
        "checkedAt": checked_at,
    }
    return finalize_probe_result(raw, previous=get_stream_health(cid, uid))


async def _run_scan() -> None:
    global _scan_task
    try:
        await hydrate_stream_health_from_db()
        active_query = {"is_active": {"$ne": False}}
        cameras = await camera_collection.find(active_query).sort("ip_address", 1).to_list(None)
        by_worker: Dict[int, list[dict]] = {}
        for camera in cameras:
            try:
                worker_id = max(1, int(camera.get("worker_id") or 1))
            except (TypeError, ValueError):
                worker_id = 1
            by_worker.setdefault(worker_id, []).append(camera)
        ordered_cameras: list[dict] = []
        while any(by_worker.values()):
            for worker_id in sorted(by_worker):
                if by_worker[worker_id]:
                    ordered_cameras.append(by_worker[worker_id].pop(0))

        _scan_state.update(
            {
                "running": True,
                "total": len(cameras),
                "completed": 0,
                "healthy": 0,
                "errors": 0,
                "suspects": 0,
                "startedAt": _iso(),
                "completedAt": None,
                "error": None,
            }
        )
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
        worker_semaphores = {
            worker_id: asyncio.Semaphore(PROBE_PER_WORKER) for worker_id in by_worker
        }

        async def probe(camera: dict, session: aiohttp.ClientSession) -> None:
            try:
                worker_id = max(1, int(camera.get("worker_id") or 1))
            except (TypeError, ValueError):
                worker_id = 1
            async with semaphore, worker_semaphores[worker_id]:
                result = await _probe_camera(session, camera)
            _store_result(result)
            await _persist_health(camera, result)
            _scan_state["completed"] += 1
            if result["ok"]:
                _scan_state["healthy"] += 1
            elif result.get("alarm"):
                _scan_state["errors"] += 1
            else:
                _scan_state["suspects"] += 1

        connector = aiohttp.TCPConnector(limit=PROBE_CONCURRENCY)
        async with aiohttp.ClientSession(connector=connector) as session:
            await asyncio.gather(*(probe(camera, session) for camera in ordered_cameras))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _scan_state["error"] = str(exc)[:500]
    finally:
        _scan_state["running"] = False
        _scan_state["completedAt"] = _iso()
        _scan_task = None


def _cache_is_stale() -> bool:
    if _scan_state.get("running") or (_scan_task and not _scan_task.done()):
        return False
    completed = _scan_state.get("completedAt")
    if not completed:
        return True
    try:
        age = (_now() - datetime.fromisoformat(completed)).total_seconds()
        return age >= CACHE_TTL_SECONDS
    except (TypeError, ValueError):
        return True


def start_stream_health_scan(*, force: bool = False) -> dict:
    """Start a non-blocking scan, unless one is already running."""
    global _scan_task
    ensure_stream_health_hydrated()
    if _scan_task and not _scan_task.done():
        return stream_health_snapshot()
    if not force and not _cache_is_stale():
        return stream_health_snapshot()
    _scan_state["running"] = True
    _scan_task = asyncio.create_task(_run_scan())
    return stream_health_snapshot()


def ensure_stream_health_scan() -> dict:
    return start_stream_health_scan(force=False)


def get_stream_health(camera_id: str, camera_uid: str = "") -> Optional[dict]:
    ensure_stream_health_hydrated()
    return _results.get(camera_id) or (camera_uid and _results.get(camera_uid)) or None


def record_stream_health(
    camera: dict,
    *,
    ok: bool,
    message: str = "",
    category: Optional[str] = None,
) -> dict:
    """Store an on-demand camera test. Manual tests alarm immediately."""
    cid = _camera_id(camera)
    ip = (camera.get("ip_address") or "").strip()
    uid = camera.get("camera_uid") or make_camera_uid(ip) or cid
    raw = {
        "cameraId": cid,
        "cameraUid": uid,
        "ok": ok,
        "category": "online" if ok else (category or classify_stream_error(message)),
        "message": "" if ok else (message or "Stream test failed"),
        "checkedAt": _iso(),
    }
    result = finalize_probe_result(raw, previous=None, force_alarm=True)
    _store_result(result)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_health(camera, result))
    except RuntimeError:
        pass
    return result


def stream_health_snapshot() -> dict:
    unique = {str(v.get("cameraId")): v for v in _results.values() if v.get("cameraId")}
    failed = sum(1 for v in unique.values() if v.get("alarm"))
    suspects = sum(1 for v in unique.values() if v.get("suspect"))
    return {
        **_scan_state,
        "cachedResults": len(unique),
        "cachedErrors": failed,
        "cachedSuspects": suspects,
        "confirmStrikes": CONFIRM_STRIKES,
        "failureMaxAgeHours": FAILURE_MAX_AGE_HOURS,
        "cacheTtlSeconds": CACHE_TTL_SECONDS,
    }


def reset_stream_health_for_tests() -> None:
    """Clear module state for unit tests."""
    global _scan_task, _hydrate_task, _hydrate_lock, _hydrated
    if _scan_task and not _scan_task.done():
        _scan_task.cancel()
    if _hydrate_task and not _hydrate_task.done():
        _hydrate_task.cancel()
    _scan_task = None
    _hydrate_task = None
    _hydrate_lock = None
    _hydrated = False
    _results.clear()
    _scan_state.update(
        {
            "running": False,
            "total": 0,
            "completed": 0,
            "healthy": 0,
            "errors": 0,
            "suspects": 0,
            "startedAt": None,
            "completedAt": None,
            "error": None,
        }
    )


async def clear_stale_stream_health_failures() -> dict:
    """Remove persisted alarm/suspect failures so Errors can rebuild cleanly."""
    global _hydrated
    result = await camera_collection.update_many(
        {"$or": [{"stream_health_ok": False}, {"stream_health_alarm": True}]},
        {
            "$unset": {
                "stream_health_ok": "",
                "stream_health_alarm": "",
                "stream_health_strikes": "",
                "stream_health_category": "",
                "stream_health_message": "",
                "stream_health_checked_at": "",
            }
        },
    )
    for key, value in list(_results.items()):
        if not value.get("ok"):
            _results.pop(key, None)
    _hydrated = False
    ensure_stream_health_hydrated()
    return {
        "ok": True,
        "cleared": int(result.modified_count or 0),
        "cachedResults": len({str(v.get("cameraId")) for v in _results.values() if v.get("cameraId")}),
    }
