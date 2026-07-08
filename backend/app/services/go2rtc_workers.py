"""
go2rtc worker registry — scale live streaming across multiple go2rtc processes.

Each worker handles up to GO2RTC_MAX_CAMERAS_PER_WORKER cameras (default 300).
Config: go2rtc/workers/<worker_id>/go2rtc.yaml
PM2 process name: go2rtc-worker-<worker_id>
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.database import camera_collection, database

logger = logging.getLogger(__name__)

workers_collection = database.get_collection("go2rtc_workers")

MAX_CAMERAS_PER_WORKER = int(os.getenv("GO2RTC_MAX_CAMERAS_PER_WORKER", "300"))
WORKERS_ENABLED = os.getenv("GO2RTC_WORKERS_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
MANAGED_BY = os.getenv("GO2RTC_MANAGED_BY", "auto").strip().lower()
WATCHDOG_INTERVAL = int(os.getenv("GO2RTC_WORKER_WATCHDOG_INTERVAL", "60"))
WATCHDOG_ENABLED = os.getenv("GO2RTC_WORKER_WATCHDOG_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
GO2RTC_DIR = Path(os.getenv("GO2RTC_DIR", str(_PROJECT_ROOT / "go2rtc"))).resolve()
GO2RTC_API_HOST = os.getenv("GO2RTC_API_HOST", "127.0.0.1").strip()
BASE_API_PORT = int(os.getenv("GO2RTC_BASE_API_PORT", "1984"))
BASE_RTSP_PORT = int(os.getenv("GO2RTC_BASE_RTSP_PORT", "8554"))
BASE_WEBRTC_PORT = int(os.getenv("GO2RTC_BASE_WEBRTC_PORT", "8555"))


def normalize_worker_id(value: Any) -> Optional[int]:
    """Accept int or legacy strings like worker-1 / go2rtc-worker-2."""
    if value is None:
        return None
    if isinstance(value, int) and value >= 1:
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        n = int(text)
        return n if n >= 1 else None
    match = re.search(r"(\d+)\s*$", text)
    if match:
        return int(match.group(1))
    return None


def worker_pm2_name(worker_id: int) -> str:
    return f"go2rtc-worker-{worker_id}"


def worker_config_path(worker_id: int) -> Path:
    return GO2RTC_DIR / "workers" / str(worker_id) / "go2rtc.yaml"


def worker_ports(worker_id: int) -> tuple[int, int, int]:
    """Return (api_port, rtsp_port, webrtc_port) for a 1-based worker id."""
    idx = max(1, int(worker_id))
    offset = idx - 1
    return (
        BASE_API_PORT + offset,
        BASE_RTSP_PORT + offset * 2,
        BASE_WEBRTC_PORT + offset * 2,
    )


def worker_base_url(worker_id: int) -> str:
    api_port, _, _ = worker_ports(worker_id)
    return f"http://{GO2RTC_API_HOST}:{api_port}"


def worker_rtsp_base(worker_id: int) -> str:
    host = os.getenv("GO2RTC_RTSP_HOST", "127.0.0.1").strip()
    _, rtsp_port, _ = worker_ports(worker_id)
    return f"rtsp://{host}:{rtsp_port}"


async def ensure_workers_indexes() -> None:
    from app.core.database import _drop_legacy_worker_id_indexes

    try:
        await _drop_legacy_worker_id_indexes(workers_collection)
        await workers_collection.create_index("worker_id", unique=True, name="idx_worker_id")
        await workers_collection.create_index("active", name="idx_worker_active")
        await _drop_legacy_worker_id_indexes(camera_collection)
        await camera_collection.create_index("worker_id", name="idx_camera_worker_id")
    except Exception as exc:
        logger.debug("[go2rtc-workers] index ensure: %s", exc)


async def get_worker(worker_id: int) -> Optional[dict]:
    doc = await workers_collection.find_one({"worker_id": int(worker_id)})
    if doc:
        return doc
    return await workers_collection.find_one({"_id": int(worker_id)})


async def list_active_workers() -> List[dict]:
    cursor = workers_collection.find({"active": {"$ne": False}}).sort("worker_id", 1)
    return await cursor.to_list(length=None)


async def _next_worker_id() -> int:
    doc = await workers_collection.find_one(sort=[("worker_id", -1)])
    if not doc:
        return 1
    return int(doc.get("worker_id") or doc.get("_id") or 0) + 1


async def _count_cameras(worker_id: int) -> int:
    wid = int(worker_id)
    return await camera_collection.count_documents(
        {
            "worker_id": wid,
            "$or": [{"is_active": True}, {"is_active": {"$exists": False}}],
        }
    )


async def _refresh_worker_camera_count(worker_id: int) -> int:
    count = await _count_cameras(worker_id)
    await workers_collection.update_one(
        {"worker_id": int(worker_id)},
        {"$set": {"assigned_camera_count": count, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return count


async def create_worker_record(worker_id: Optional[int] = None) -> dict:
    wid = int(worker_id or await _next_worker_id())
    api_port, rtsp_port, webrtc_port = worker_ports(wid)
    base_url = worker_base_url(wid)
    config_path = worker_config_path(wid)
    doc = {
        "_id": wid,
        "worker_id": wid,
        "pm2_name": worker_pm2_name(wid),
        "base_url": base_url,
        "api_port": api_port,
        "rtsp_port": rtsp_port,
        "webrtc_port": webrtc_port,
        "active": True,
        "assigned_camera_count": 0,
        "max_cameras": MAX_CAMERAS_PER_WORKER,
        "config_path": str(config_path.relative_to(_PROJECT_ROOT)).replace("\\", "/"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await workers_collection.update_one({"worker_id": wid}, {"$set": doc}, upsert=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[go2rtc-workers] Created worker %s api=%s rtsp=%s", wid, api_port, rtsp_port)
    return doc


async def ensure_default_worker() -> dict:
    workers = await list_active_workers()
    if workers:
        return workers[0]
    return await create_worker_record(1)


async def assign_worker_for_new_camera(preferred: Any = None) -> int:
    """
    Pick least-loaded active worker under MAX_CAMERAS_PER_WORKER, or create a new worker.
    """
    preferred_id = normalize_worker_id(preferred)
    if preferred_id:
        worker = await get_worker(preferred_id)
        if worker and worker.get("active") is not False:
            count = await _count_cameras(preferred_id)
            if count < MAX_CAMERAS_PER_WORKER:
                return preferred_id

    workers = await list_active_workers()
    if not workers:
        worker = await create_worker_record(1)
        return int(worker["worker_id"])

    best_id: Optional[int] = None
    best_count = MAX_CAMERAS_PER_WORKER + 1
    for row in workers:
        wid = int(row["worker_id"])
        count = await _count_cameras(wid)
        await workers_collection.update_one(
            {"worker_id": wid},
            {"$set": {"assigned_camera_count": count}},
        )
        if count < MAX_CAMERAS_PER_WORKER and count < best_count:
            best_id = wid
            best_count = count

    if best_id is not None:
        return best_id

    new_id = await _next_worker_id()
    await create_worker_record(new_id)
    return new_id


async def get_worker_id_for_camera_doc(cam: Optional[dict]) -> int:
    if not cam:
        worker = await ensure_default_worker()
        return int(worker["worker_id"])
    wid = normalize_worker_id(cam.get("worker_id"))
    if wid:
        if await get_worker(wid):
            return wid
    worker = await ensure_default_worker()
    return int(worker["worker_id"])


async def get_api_url_for_camera_doc(cam: Optional[dict]) -> str:
    if not WORKERS_ENABLED:
        from app.services.go2rtc_service import GO2RTC_API_URL

        return GO2RTC_API_URL
    wid = await get_worker_id_for_camera_doc(cam)
    return worker_base_url(wid)


async def get_api_url_for_stream(src: str, cam_doc: Optional[dict] = None) -> str:
    if not WORKERS_ENABLED:
        from app.services.go2rtc_service import GO2RTC_API_URL

        return GO2RTC_API_URL
    if cam_doc is None:
        from app.services.camera_identity import get_camera_by_ref
        from app.services.camera_access import parse_stream_camera_id

        ref = parse_stream_camera_id(src)
        if ref:
            cam_doc = await get_camera_by_ref(ref)
    return await get_api_url_for_camera_doc(cam_doc)


async def get_default_player_api_url() -> str:
    """Player static assets — any healthy worker serves the same JS."""
    workers = await list_active_workers()
    if workers:
        return str(workers[0].get("base_url") or worker_base_url(int(workers[0]["worker_id"])))
    from app.services.go2rtc_service import GO2RTC_API_URL

    return GO2RTC_API_URL


def _pm2_available() -> bool:
    return shutil.which("pm2") is not None


def _use_pm2() -> bool:
    if MANAGED_BY == "pm2":
        return True
    if MANAGED_BY == "subprocess":
        return False
    return _pm2_available()


async def _run_cmd(cmd: List[str], *, timeout: float = 60.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_PROJECT_ROOT),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    return (
        proc.returncode or 0,
        (stdout_b or b"").decode("utf-8", errors="replace"),
        (stderr_b or b"").decode("utf-8", errors="replace"),
    )


async def pm2_worker_running(worker_id: int) -> bool:
    if not _use_pm2():
        return False
    code, out, _ = await _run_cmd(["pm2", "jlist"], timeout=15.0)
    if code != 0:
        return False
    import json

    try:
        apps = json.loads(out)
    except json.JSONDecodeError:
        return False
    name = worker_pm2_name(worker_id)
    for app in apps:
        if app.get("name") == name and app.get("pm2_env", {}).get("status") == "online":
            return True
    return False


async def pm2_save() -> None:
    if not _use_pm2():
        return
    code, _, err = await _run_cmd(["pm2", "save"], timeout=20.0)
    if code != 0:
        logger.debug("[go2rtc-workers] pm2 save: %s", err)


async def pm2_start_worker(worker_id: int) -> dict:
    from app.services.go2rtc_service import go2rtc_bin

    if not _use_pm2():
        return {"ok": False, "skipped": True, "reason": "pm2 not available"}

    if await pm2_worker_running(worker_id):
        return {"ok": True, "reused": True, "worker_id": worker_id}

    binary = go2rtc_bin()
    if not binary.is_file():
        return {"ok": False, "error": f"go2rtc binary not found: {binary}"}

    config = worker_config_path(worker_id)
    if not config.is_file():
        return {"ok": False, "error": f"config missing: {config}"}

    name = worker_pm2_name(worker_id)
    start_cmd = [
        "pm2",
        "start",
        str(binary),
        "--name",
        name,
        "--cwd",
        str(GO2RTC_DIR),
        "--",
        "-config",
        str(config),
    ]
    code, out, err = await _run_cmd(start_cmd, timeout=30.0)
    ok = code == 0
    if not ok and "already exists" in f"{out} {err}".lower():
        code, out, err = await _run_cmd(["pm2", "delete", name], timeout=20.0)
        if code == 0:
            code, out, err = await _run_cmd(start_cmd, timeout=30.0)
            ok = code == 0
    if ok:
        logger.info("[go2rtc-workers] PM2 started %s", name)
        if worker_id >= 2:
            await pm2_save()
    else:
        logger.warning("[go2rtc-workers] PM2 start failed %s: %s %s", name, out, err)
    return {"ok": ok, "worker_id": worker_id, "pm2_name": name, "stdout": out, "stderr": err}


async def pm2_reload_worker(worker_id: int) -> dict:
    if not _use_pm2():
        return {"ok": False, "skipped": True, "reason": "pm2 not available"}

    name = worker_pm2_name(worker_id)
    if not await pm2_worker_running(worker_id):
        return await pm2_start_worker(worker_id)

    code, out, err = await _run_cmd(["pm2", "reload", name], timeout=45.0)
    ok = code == 0
    if ok:
        logger.info("[go2rtc-workers] PM2 reloaded %s", name)
    else:
        logger.warning("[go2rtc-workers] PM2 reload failed %s: %s %s", name, out, err)
    return {"ok": ok, "worker_id": worker_id, "pm2_name": name, "stdout": out, "stderr": err}


_worker_procs: Dict[int, asyncio.subprocess.Process] = {}
_watchdog_task: Optional[asyncio.Task] = None


async def subprocess_stop_worker(worker_id: int) -> None:
    """Stop a backend-managed go2rtc subprocess (no-op if started externally e.g. PM2)."""
    global _worker_procs
    proc = _worker_procs.pop(int(worker_id), None)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logger.info("[go2rtc-workers] Stopped subprocess worker %s", worker_id)


async def subprocess_start_worker(worker_id: int) -> dict:
    from app.services.go2rtc_service import go2rtc_bin, is_api_healthy

    if _use_pm2():
        return await pm2_start_worker(worker_id)

    api_url = worker_base_url(worker_id)
    if await is_api_healthy(api_url):
        return {"ok": True, "reused": True, "worker_id": worker_id}

    proc = _worker_procs.get(worker_id)
    if proc and proc.returncode is None:
        return {"ok": True, "reused": True, "worker_id": worker_id, "pid": proc.pid}

    binary = go2rtc_bin()
    config = worker_config_path(worker_id)
    if not binary.is_file() or not config.is_file():
        return {"ok": False, "error": "binary or config missing"}

    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "-config",
            str(config),
            cwd=str(GO2RTC_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    _worker_procs[worker_id] = proc
    for _ in range(40):
        await asyncio.sleep(0.25)
        if await is_api_healthy(api_url):
            logger.info("[go2rtc-workers] Subprocess worker %s pid=%s", worker_id, proc.pid)
            return {"ok": True, "worker_id": worker_id, "pid": proc.pid}
        if proc.returncode is not None:
            err = ""
            if proc.stderr:
                err = (await proc.stderr.read()).decode("utf-8", errors="replace")[:400]
            return {"ok": False, "error": f"exited rc={proc.returncode}", "detail": err}
    return {"ok": False, "error": "API not ready within 10s", "pid": proc.pid}


async def reload_worker_process(worker_id: int, *, reason: str = "config") -> dict:
    """Start or reload only one worker — never restarts the full fleet."""
    if _use_pm2():
        return await pm2_reload_worker(worker_id)
    from app.services.go2rtc_service import is_api_healthy, sync_streams_to_go2rtc, write_worker_config_file

    built = await write_worker_config_file(worker_id)
    if not built.get("ok"):
        return {"ok": False, "worker_id": worker_id, "error": built.get("error")}

    api_url = worker_base_url(worker_id)
    if await is_api_healthy(api_url):
        sync = await sync_streams_to_go2rtc(built.get("streams") or {}, api_url=api_url)
        return {"ok": bool(sync.get("ok")), "worker_id": worker_id, "sync": sync, "reason": reason}

    started = await subprocess_start_worker(worker_id)
    if not started.get("ok"):
        return started
    sync = await sync_streams_to_go2rtc(built.get("streams") or {}, api_url=api_url)
    return {"ok": bool(sync.get("ok")), "worker_id": worker_id, "sync": sync, "started": started}


async def ensure_workers_for_assigned_cameras() -> List[int]:
    """Ensure go2rtc_workers records exist for every worker_id assigned to active cameras."""
    worker_ids: set[int] = set()
    query = {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]}
    async for cam in camera_collection.find(query, {"worker_id": 1}):
        wid = normalize_worker_id(cam.get("worker_id"))
        if wid:
            worker_ids.add(wid)

    if not worker_ids:
        await ensure_default_worker()
        return [1]

    for wid in sorted(worker_ids):
        if not await get_worker(wid):
            await create_worker_record(wid)
            logger.info("[go2rtc-workers] Created missing worker record %s from camera assignments", wid)
            if _use_pm2() and wid >= 2:
                await pm2_start_worker(wid)
    return sorted(worker_ids)


async def stop_legacy_monolithic_go2rtc() -> None:
    """Stop the pre-worker single-process go2rtc so worker ports are not hijacked."""
    from app.services.go2rtc_service import stop_legacy_go2rtc_subprocess

    await stop_legacy_go2rtc_subprocess()


async def sync_worker(worker_id: int, *, reload_pm2: bool = False) -> dict:
    from app.services.go2rtc_service import (
        fetch_go2rtc_streams,
        is_api_healthy,
        sync_streams_to_go2rtc,
        write_worker_config_file,
    )

    built = await write_worker_config_file(worker_id)
    if not built.get("ok"):
        return {**built, "worker_id": worker_id}

    api_url = worker_base_url(worker_id)
    if not await is_api_healthy(api_url):
        started = await subprocess_start_worker(worker_id)
        if not started.get("ok"):
            return {"ok": False, "worker_id": worker_id, "error": started.get("error"), "started": started}

    streams = built.get("streams") or {}
    expected = len(streams)
    sync = await sync_streams_to_go2rtc(streams, api_url=api_url)

    pm2_result = None
    subprocess_restarted = False
    live = await fetch_go2rtc_streams(api_url)
    live_count = len(live) if isinstance(live, dict) else 0
    missing = max(0, expected - live_count)
    excess = max(0, live_count - expected)
    missing_threshold = max(4, expected // 50)
    excess_threshold = max(10, expected // 10)
    drift = excess >= excess_threshold
    under_sync = missing >= missing_threshold
    needs_reload = reload_pm2 or drift or under_sync
    if needs_reload:
        if drift or under_sync:
            logger.warning(
                "[go2rtc-workers] Worker %s stream mismatch: live=%s expected=%s "
                "(missing=%s excess=%s) — reloading",
                worker_id,
                live_count,
                expected,
                missing,
                excess,
            )
        if _use_pm2():
            pm2_result = await pm2_reload_worker(worker_id)
            if pm2_result.get("ok"):
                sync = await sync_streams_to_go2rtc(streams, api_url=api_url)
                live = await fetch_go2rtc_streams(api_url)
                live_count = len(live) if isinstance(live, dict) else 0
        elif drift or under_sync or reload_pm2:
            await subprocess_stop_worker(worker_id)
            started = await subprocess_start_worker(worker_id)
            subprocess_restarted = bool(started.get("ok"))
            if started.get("ok"):
                sync = await sync_streams_to_go2rtc(streams, api_url=api_url)
                live = await fetch_go2rtc_streams(api_url)
                live_count = len(live) if isinstance(live, dict) else 0
            else:
                logger.warning(
                    "[go2rtc-workers] Worker %s subprocess restart failed: %s",
                    worker_id,
                    started.get("error"),
                )

    await _refresh_worker_camera_count(worker_id)

    return {
        "ok": bool(sync.get("ok")),
        "worker_id": worker_id,
        "apiUrl": api_url,
        "streamCount": built.get("streamCount"),
        "liveStreamCount": live_count,
        "cameraCount": built.get("cameraCount"),
        "sync": sync,
        "pm2": pm2_result,
        "subprocessRestarted": subprocess_restarted,
        "driftReloaded": bool(
            needs_reload
            and (
                (pm2_result and pm2_result.get("ok"))
                or subprocess_restarted
            )
        ),
        "missingStreams": missing,
        "excessStreams": excess,
    }


async def sync_all_workers() -> dict:
    workers = await list_active_workers()
    if not workers:
        await ensure_default_worker()
        workers = await list_active_workers()

    results = []
    ok = True
    for row in workers:
        wid = int(row["worker_id"])
        result = await sync_worker(wid)
        results.append(result)
        ok = ok and bool(result.get("ok"))

    return {"ok": ok, "workers": results, "workerCount": len(results)}


async def ensure_camera_worker_assigned(
    fields: dict,
    *,
    existing: Optional[dict] = None,
) -> dict:
    """Assign worker_id for new cameras; preserve existing assignment on update."""
    if not WORKERS_ENABLED:
        return fields
    out = dict(fields)
    if existing:
        wid = normalize_worker_id(existing.get("worker_id"))
        if wid:
            out["worker_id"] = wid
            return out
    preferred = normalize_worker_id(out.get("worker_id"))
    out["worker_id"] = await assign_worker_for_new_camera(preferred)
    return out


async def migrate_cameras_without_worker() -> int:
    """Assign worker_id to active cameras missing it."""
    await ensure_default_worker()
    migrated = 0
    query = {
        "$and": [
            {
                "$or": [
                    {"worker_id": {"$exists": False}},
                    {"worker_id": None},
                    {"worker_id": ""},
                ]
            },
            {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
        ]
    }
    async for cam in camera_collection.find(query):
        wid = await assign_worker_for_new_camera()
        await camera_collection.update_one(
            {"_id": cam["_id"]},
            {"$set": {"worker_id": wid}},
        )
        migrated += 1

    # Normalize legacy string worker ids (worker-1) to integers.
    async for cam in camera_collection.find({"worker_id": {"$type": "string"}}):
        wid = normalize_worker_id(cam.get("worker_id"))
        if wid:
            await camera_collection.update_one(
                {"_id": cam["_id"]},
                {"$set": {"worker_id": wid}},
            )
            migrated += 1

    if migrated:
        logger.info("[go2rtc-workers] Migrated %s camera(s) worker_id", migrated)
    workers = await list_active_workers()
    for row in workers:
        await _refresh_worker_camera_count(int(row["worker_id"]))
    return migrated


async def heal_worker(worker_id: int) -> dict:
    """
    Ensure one worker API is up and stream count matches MongoDB.
    Restarts/resyncs automatically when unhealthy or under-synced.
    """
    from app.services.go2rtc_service import (
        build_all_streams_config,
        fetch_go2rtc_streams,
        is_api_healthy_retry,
        write_worker_config_file,
    )

    wid = int(worker_id)
    built = await build_all_streams_config(wid)
    if not built.get("ok"):
        return {"ok": False, "worker_id": wid, "error": built.get("error")}

    expected = len(built.get("streams") or {})
    api_url = worker_base_url(wid)
    healthy = await is_api_healthy_retry(api_url)

    if not healthy:
        logger.warning("[go2rtc-workers] Worker %s API down after retries — starting and syncing", wid)
        await write_worker_config_file(wid)
        started = await subprocess_start_worker(wid)
        if not started.get("ok"):
            return {"ok": False, "worker_id": wid, "action": "start_failed", **started}
        return await sync_worker(wid)

    live = await fetch_go2rtc_streams(api_url)
    live_count = len(live) if isinstance(live, dict) else 0
    missing = max(0, expected - live_count)
    excess = max(0, live_count - expected)
    missing_threshold = max(4, expected // 50)
    excess_threshold = max(10, expected // 10)

    if missing >= missing_threshold or excess >= excess_threshold:
        return await sync_worker(wid, reload_pm2=True)

    return {
        "ok": True,
        "worker_id": wid,
        "healthy": True,
        "expectedStreams": expected,
        "liveStreams": live_count,
    }


async def heal_all_workers() -> dict:
    workers = await list_active_workers()
    results = []
    ok = True
    for row in workers:
        wid = int(row["worker_id"])
        result = await heal_worker(wid)
        results.append(result)
        ok = ok and bool(result.get("ok"))
    return {"ok": ok, "workers": results}


async def _watchdog_loop() -> None:
    # Avoid false "worker down" alerts while startup sync is still running.
    await asyncio.sleep(max(30, WATCHDOG_INTERVAL // 2))
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if not WORKERS_ENABLED:
                continue
            for row in await list_active_workers():
                wid = int(row["worker_id"])
                try:
                    result = await heal_worker(wid)
                    if not result.get("ok"):
                        logger.warning(
                            "[go2rtc-workers] Watchdog heal failed worker %s: %s",
                            wid,
                            result.get("error") or result,
                        )
                except Exception as exc:
                    logger.warning("[go2rtc-workers] Watchdog error worker %s: %s", wid, exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("[go2rtc-workers] Watchdog loop error: %s", exc)


def start_worker_watchdog() -> None:
    """Background task: keep every go2rtc worker healthy (auto-restart + resync)."""
    global _watchdog_task
    if not WORKERS_ENABLED or not WATCHDOG_ENABLED:
        return
    if _watchdog_task and not _watchdog_task.done():
        return
    _watchdog_task = asyncio.create_task(_watchdog_loop())
    logger.info("[go2rtc-workers] Watchdog started (interval=%ss)", WATCHDOG_INTERVAL)


async def stop_worker_watchdog() -> None:
    global _watchdog_task
    if _watchdog_task:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None


async def startup_workers() -> dict:
    """Migrate worker assignments, (re)start worker processes, and push DB → go2rtc API."""
    if not WORKERS_ENABLED:
        return {"ok": True, "skipped": True}

    await ensure_workers_indexes()
    await migrate_cameras_without_worker()
    await ensure_workers_for_assigned_cameras()
    await stop_legacy_monolithic_go2rtc()

    workers = await list_active_workers()
    if not workers:
        await create_worker_record(1)

    # Write yaml, ensure each worker process is up, push streams, and DELETE stale entries.
    result = await sync_all_workers()
    if result.get("ok"):
        logger.info(
            "[go2rtc-workers] Startup sync complete: %s worker(s)",
            result.get("workerCount"),
        )
    else:
        logger.warning("[go2rtc-workers] Startup sync had errors: %s", result)

    start_worker_watchdog()
    return result


async def stop_all_worker_subprocesses() -> None:
    await stop_worker_watchdog()
    global _worker_procs
    for wid, proc in list(_worker_procs.items()):
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        logger.info("[go2rtc-workers] Stopped subprocess worker %s", wid)
    _worker_procs.clear()
