"""
go2rtc relay — live view engine (WebRTC/MSE via go2rtc proxy).

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
from app.services.rtsp_utils import mask_rtsp_url, stream_source_urls

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
GO2RTC_API_HOST = os.getenv("GO2RTC_API_HOST", "127.0.0.1").strip()
GO2RTC_API_PORT = int(os.getenv("GO2RTC_API_PORT", "1984"))
GO2RTC_API_URL = f"http://{GO2RTC_API_HOST}:{GO2RTC_API_PORT}"

_proc: Optional[asyncio.subprocess.Process] = None
_consumer_counts: Dict[str, int] = {}
_webrtc_host_resolved: Optional[str] = None
_webrtc_host_logged = False


def _guess_lan_ip() -> Optional[str]:
    """Best-effort LAN IP for WebRTC when GO2RTC_WEBRTC_HOST is unset."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


def resolve_webrtc_host() -> str:
    """WebRTC ICE host — env override, else auto-detected LAN IP, else API host."""
    global _webrtc_host_resolved, _webrtc_host_logged
    if _webrtc_host_resolved:
        return _webrtc_host_resolved

    explicit = os.getenv("GO2RTC_WEBRTC_HOST", "").strip()
    if explicit:
        host = explicit
    else:
        host = _guess_lan_ip() or GO2RTC_API_HOST

    _webrtc_host_resolved = host
    if not _webrtc_host_logged:
        _webrtc_host_logged = True
        if host in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "[go2rtc] WebRTC candidates use %s — set GO2RTC_WEBRTC_HOST to the server LAN IP "
                "for remote browsers",
                host,
            )
        elif not explicit:
            logger.info("[go2rtc] WebRTC candidates use auto-detected LAN IP %s", host)
    return host


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


def local_recording_rtsp_url(
    camera_uid: str,
    stream: str = "main",
    *,
    worker_id: Optional[int] = None,
) -> str:
    """RTSP URL via go2rtc relay — avoids extra direct connections to the camera."""
    host = os.getenv("GO2RTC_RTSP_HOST", "127.0.0.1").strip()
    profile = "main" if (stream or "main").strip().lower() == "main" else "sub"
    if worker_id is not None:
        from app.services.go2rtc_workers import worker_ports

        _, rtsp_port, _ = worker_ports(int(worker_id))
        port = str(rtsp_port)
    else:
        port = os.getenv("GO2RTC_RTSP_PORT", "8554").strip()
    return f"rtsp://{host}:{port}/{stream_name(camera_uid, profile)}"


def _rtsp_with_tcp(url: str) -> str:
    if not url:
        return url
    # go2rtc ffmpeg: sources already carry #video=… options — do not rewrite.
    if url.startswith("ffmpeg:"):
        return url
    timeout = os.getenv("GO2RTC_RTSP_TIMEOUT", "20").strip()
    base, _, frag = url.partition("#")
    params: Dict[str, str] = {}
    if frag:
        for piece in frag.replace("#", "&").split("&"):
            piece = piece.strip()
            if not piece:
                continue
            if "=" in piece:
                k, v = piece.split("=", 1)
                params[k.strip()] = v.strip()
            else:
                params[piece] = ""
    params.setdefault("rtsp_transport", "tcp")
    if timeout and "timeout" not in params:
        params["timeout"] = timeout
    query = "&".join(
        f"{k}={v}" if v != "" else k for k, v in params.items()
    )
    return f"{base}#{query}" if query else base


async def _all_cameras(worker_id: Optional[int] = None) -> List[dict]:
    query: Dict[str, Any] = {
        "$or": [
            {"is_active": True},
            {"is_active": {"$exists": False}},
        ]
    }
    if worker_id is not None:
        from app.services.go2rtc_workers import normalize_worker_id

        wid = int(worker_id)
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"worker_id": wid},
                        {"worker_id": str(wid)},
                        {"worker_id": f"worker-{wid}"},
                    ]
                },
            ]
        }
    cursor = camera_collection.find(query).sort("name", 1)
    return await cursor.to_list(length=None)


async def build_all_streams_config(worker_id: Optional[int] = None) -> Dict[str, Any]:
    """Build go2rtc stream map for every camera (optionally filtered by worker)."""
    cameras = await _all_cameras(worker_id)
    streams: Dict[str, str] = {}
    masked: Dict[str, str] = {}
    cameras_meta: List[dict] = []
    errors: List[str] = []

    for cam in cameras:
        camera_id = str(cam["_id"])
        camera_uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "") or camera_id
        name = cam.get("name") or camera_id
        sub_sources = [_rtsp_with_tcp(u) for u in stream_source_urls(cam, main=False) if u]
        main_sources = [_rtsp_with_tcp(u) for u in stream_source_urls(cam, main=True) if u]
        sub_url = sub_sources[0] if sub_sources else ""
        main_url = main_sources[0] if main_sources else ""

        sub_key = stream_name(camera_uid, "sub")
        main_key = stream_name(camera_uid, "main")

        if sub_sources:
            streams[sub_key] = sub_sources[0] if len(sub_sources) == 1 else sub_sources
            masked[sub_key] = mask_rtsp_url(sub_sources[0])
        else:
            errors.append(f"{name}: missing sub stream URL")

        if main_sources:
            streams[main_key] = main_sources[0] if len(main_sources) == 1 else main_sources
            masked[main_key] = mask_rtsp_url(main_sources[0])
        else:
            errors.append(f"{name}: missing main stream URL")

        cameras_meta.append(
            {
                "cameraId": camera_id,
                "cameraUid": camera_uid,
                "cameraName": name,
                "workerId": cam.get("worker_id"),
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


def _webrtc_candidates(webrtc_port: int) -> List[str]:
    """ICE candidates for browser WebRTC — must be reachable from clients (not 127.0.0.1 on GPU server)."""
    return [f"{resolve_webrtc_host()}:{webrtc_port}"]


def _dump_go2rtc_yaml(data: dict) -> str:
    """Dump go2rtc config; quote stream URLs so `&` in query strings is not a YAML alias."""

    class _Dumper(yaml.SafeDumper):
        pass

    def _represent_str(dumper: yaml.SafeDumper, value: str):
        style = (
            "'"
            if (
                "&" in value
                or value.startswith(("rtsp://", "onvif://", "ffmpeg:"))
            )
            else None
        )
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    _Dumper.add_representer(str, _represent_str)
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True)


def _base_yaml(
    streams: Dict[str, str],
    *,
    api_port: int,
    rtsp_port: int,
    webrtc_port: int,
) -> dict:
    return {
        "api": {"listen": f"{GO2RTC_API_HOST}:{api_port}"},
        "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
        "webrtc": {
            "listen": f":{webrtc_port}",
            "candidates": _webrtc_candidates(webrtc_port),
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


async def write_worker_config_file(worker_id: int) -> Dict[str, Any]:
    from app.services.go2rtc_workers import worker_config_path, worker_ports

    built = await build_all_streams_config(worker_id)
    if not built.get("ok"):
        return built

    api_port, rtsp_port, webrtc_port = worker_ports(worker_id)
    config_path = worker_config_path(worker_id)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _dump_go2rtc_yaml(
            _base_yaml(
                built["streams"],
                api_port=api_port,
                rtsp_port=rtsp_port,
                webrtc_port=webrtc_port,
            )
        ),
        encoding="utf-8",
    )
    logger.info(
        "[go2rtc] Wrote worker %s config %s cameras=%s streams=%s",
        worker_id,
        config_path,
        built.get("cameraCount"),
        built.get("streamCount"),
    )
    built["workerId"] = worker_id
    built["configPath"] = str(config_path)
    return built


async def write_config_file() -> Dict[str, Any]:
    from app.services.go2rtc_workers import WORKERS_ENABLED, list_active_workers, write_worker_config_file

    if WORKERS_ENABLED:
        workers = await list_active_workers()
        if not workers:
            return await write_worker_config_file(1)
        combined: Dict[str, Any] = {
            "ok": True,
            "streamCount": 0,
            "cameraCount": 0,
            "streams": {},
            "masked": {},
            "cameras": [],
            "errors": [],
        }
        for row in workers:
            wid = int(row["worker_id"])
            built = await write_worker_config_file(wid)
            if not built.get("ok"):
                return built
            combined["streamCount"] += int(built.get("streamCount") or 0)
            combined["cameraCount"] += int(built.get("cameraCount") or 0)
            combined["streams"].update(built.get("streams") or {})
            combined["masked"].update(built.get("masked") or {})
            combined["cameras"].extend(built.get("cameras") or [])
            combined["errors"].extend(built.get("errors") or [])
        return combined
    built = await build_all_streams_config()
    if not built.get("ok"):
        return built

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        _dump_go2rtc_yaml(
            _base_yaml(
                built["streams"],
                api_port=GO2RTC_API_PORT,
                rtsp_port=int(os.getenv("GO2RTC_RTSP_PORT", "8554")),
                webrtc_port=int(os.getenv("GO2RTC_WEBRTC_PORT", "8555")),
            )
        ),
        encoding="utf-8",
    )
    logger.info(
        "[go2rtc] Wrote config %s cameras=%s streams=%s",
        CONFIG_PATH,
        built.get("cameraCount"),
        built.get("streamCount"),
    )
    return built


async def is_api_healthy(api_url: Optional[str] = None, *, timeout_sec: float = 4.0) -> bool:
    base = (api_url or GO2RTC_API_URL).rstrip("/")
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/api") as resp:
                return resp.status == 200
    except Exception:
        return False


async def is_api_healthy_retry(
    api_url: str,
    *,
    attempts: int = 3,
    delay_sec: float = 1.0,
) -> bool:
    for attempt in range(max(1, attempts)):
        if await is_api_healthy(api_url):
            return True
        if attempt + 1 < attempts:
            await asyncio.sleep(delay_sec)
    return False


async def fetch_go2rtc_streams(api_url: Optional[str] = None) -> Dict[str, Any]:
    """GET go2rtc /api/streams — producers/consumers per stream."""
    base = (api_url or GO2RTC_API_URL).rstrip("/")
    try:
        timeout = aiohttp.ClientTimeout(total=15.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/api/streams") as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except Exception as exc:
        logger.debug("[go2rtc] streams API %s: %s", base, exc)
        return {}


def _normalize_stream_sources(desired_src: Any) -> List[str]:
    """Accept a single RTSP URL or a go2rtc failover list."""
    if isinstance(desired_src, (list, tuple)):
        return [str(s).strip() for s in desired_src if str(s).strip()]
    text = str(desired_src or "").strip()
    return [text] if text else []


def _src_for_go2rtc_api(url: str) -> str:
    """Return the source unchanged; aiohttp encodes it as one query value.

    Pre-encoding inner ampersands produces a literal ``%26`` in go2rtc's RTSP
    URL (for example ``ptype=tcp%26dev=1``), which cameras reject.
    """
    return (url or "").strip()


def _producer_url_matches(existing_info: Any, desired_src: Any) -> bool:
    """True when go2rtc already has the primary source configured.

    Only the first desired URL is compared. Matching against fallbacks would
    skip updates when an old primary (e.g. /h264) is still listed as a failover.
    """
    if isinstance(existing_info, (list, tuple)):
        # Rare: go2rtc may return the configured source list instead of producer dicts.
        producers = [{"url": existing_info[0]}] if existing_info else []
    elif isinstance(existing_info, dict):
        producers = existing_info.get("producers") or []
    else:
        return False
    if not producers:
        return False
    first = producers[0]
    if isinstance(first, dict):
        raw_url = first.get("url")
    else:
        raw_url = first
    # Failover configs can surface url as a list — take the primary entry.
    if isinstance(raw_url, (list, tuple)):
        raw_url = raw_url[0] if raw_url else ""
    cur = str(raw_url or "").strip()
    wants = _normalize_stream_sources(desired_src)
    if not cur or not wants:
        return False
    primary = wants[0]
    cur_base = cur.split("#", 1)[0]
    want_base = primary.split("#", 1)[0]

    def _norm(u: str) -> str:
        return u.replace("%26", "&")

    return (
        cur == primary
        or cur_base == want_base
        or _norm(cur_base) == _norm(want_base)
    )


async def sync_streams_to_go2rtc(
    streams: Dict[str, str],
    *,
    api_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Push MongoDB stream map into a running go2rtc instance.

    Needed when go2rtc was started with an old pilot config (e.g. Cam18_sub)
    while the UI requests {cameraObjectId}_sub.
    """
    if not streams:
        return {
            "ok": False,
            "error": "no streams",
            "added": 0,
            "updated": 0,
            "removed": 0,
            "missingCount": 0,
        }

    base = (api_url or GO2RTC_API_URL).rstrip("/")
    desired = set(streams.keys())
    total = len(streams)
    # Scale timeout for large fleets (~1038 streams need more than 120s under load).
    timeout_sec = max(120.0, min(600.0, 60.0 + total * 0.15))
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    added = 0
    updated = 0
    removed = 0
    skipped = 0
    errors: List[str] = []
    sem = asyncio.Semaphore(12)

    async def _put_batch(
        session: aiohttp.ClientSession,
        batch: Dict[str, str],
        existing: Dict[str, Any],
        existing_names: set[str],
    ) -> None:
        nonlocal added, updated, skipped

        async def _put_one(name: str, src: Any) -> None:
            nonlocal added, updated, skipped
            info = existing.get(name) or {}
            sources = _normalize_stream_sources(src)
            if not sources:
                return
            if name in existing_names and _producer_url_matches(info, sources):
                skipped += 1
                return
            async with sem:
                try:
                    async with session.put(
                        f"{base}/api/streams",
                        # go2rtc's query API accepts one src. Repeated src params
                        # can corrupt its runtime YAML; failover lists are loaded
                        # from the worker config on process reload.
                        params={
                            "name": name,
                            "src": _src_for_go2rtc_api(sources[0]),
                        },
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

        await asyncio.gather(*[_put_one(name, src) for name, src in batch.items()])

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(3):
            existing = await fetch_go2rtc_streams(base)
            existing_names = set(existing.keys()) if isinstance(existing, dict) else set()

            if existing_names == desired and all(
                _producer_url_matches(existing.get(name) or {}, streams[name]) for name in desired
            ):
                return {
                    "ok": len(errors) == 0,
                    "added": added,
                    "updated": updated,
                    "removed": removed,
                    "skipped": skipped,
                    "missingCount": 0,
                    "errors": errors,
                }

            pending = {
                name: streams[name]
                for name in sorted(desired)
                if name not in existing_names
                or not _producer_url_matches(existing.get(name) or {}, streams[name])
            }
            if pending:
                if attempt:
                    logger.warning(
                        "[go2rtc] Retry %s: pushing %s missing/stale stream(s)",
                        attempt + 1,
                        len(pending),
                    )
                await _put_batch(session, pending, existing, existing_names)

            post = await fetch_go2rtc_streams(base)
            post_names = set(post.keys()) if isinstance(post, dict) else set()
            still_missing = sorted(desired - post_names)
            if not still_missing:
                break
            if attempt == 2:
                logger.error(
                    "[go2rtc] %s stream(s) still missing after sync: %s",
                    len(still_missing),
                    ", ".join(still_missing[:5]),
                )

        final = await fetch_go2rtc_streams(base)
        final_names = set(final.keys()) if isinstance(final, dict) else set()
        for name in sorted(final_names - desired):
            try:
                async with session.delete(
                    f"{base}/api/streams",
                    params={"src": name},
                ) as resp:
                    if resp.status in (200, 204):
                        removed += 1
                    else:
                        body = await resp.text()
                        errors.append(f"delete {name}: HTTP {resp.status} {body[:120]}")
            except Exception as exc:
                errors.append(f"delete {name}: {exc}")

    post = await fetch_go2rtc_streams(base)
    post_names = set(post.keys()) if isinstance(post, dict) else set()
    missing = sorted(desired - post_names)

    if added or updated or removed:
        logger.info(
            "[go2rtc] Stream sync added=%s updated=%s removed=%s skipped=%s missing=%s",
            added,
            updated,
            removed,
            skipped,
            len(missing),
        )
    elif skipped and not missing:
        logger.debug("[go2rtc] Stream sync skipped %s unchanged stream(s)", skipped)

    if errors:
        logger.warning("[go2rtc] Stream sync errors: %s", errors[:5])

    return {
        "ok": len(errors) == 0 and not missing,
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped": skipped,
        "missingCount": len(missing),
        "missingStreams": missing[:50],
        "errors": errors,
    }


async def _restart_go2rtc_api() -> bool:
    """Ask running go2rtc to restart (drops active viewers). Legacy monolithic mode only."""
    from app.services.go2rtc_workers import WORKERS_ENABLED

    if WORKERS_ENABLED:
        return False
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


def ui_consumer_count(stream: str) -> int:
    return int(_consumer_counts.get(stream, 0) or 0)


async def start_go2rtc(*, reload: bool = False) -> Dict[str, Any]:
    global _proc

    if not GO2RTC_ENABLED:
        return {"ok": False, "error": "GO2RTC_ENABLED=false", "running": False}

    from app.services.go2rtc_workers import WORKERS_ENABLED, list_active_workers, reload_worker_process, sync_all_workers

    if WORKERS_ENABLED:
        if reload:
            results = []
            for row in await list_active_workers():
                wid = int(row["worker_id"])
                results.append(await reload_worker_process(wid, reason="admin_reload"))
            return {
                "ok": all(r.get("ok") for r in results) if results else False,
                "workers": results,
            }
        return await sync_all_workers()

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


async def stop_legacy_go2rtc_subprocess() -> None:
    """Stop only the legacy single-process go2rtc (runtime/go2rtc.yaml), not PM2 workers."""
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
        logger.info("[go2rtc] Stopped legacy subprocess pid=%s", proc.pid)


async def stop_go2rtc() -> None:
    from app.services.go2rtc_workers import WORKERS_ENABLED, stop_all_worker_subprocesses

    if WORKERS_ENABLED:
        await stop_all_worker_subprocesses()

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


async def _streams_by_worker() -> Dict[int, Dict[str, Any]]:
    from app.services.go2rtc_workers import WORKERS_ENABLED, list_active_workers, worker_base_url

    if not WORKERS_ENABLED:
        return {1: await fetch_go2rtc_streams()}

    out: Dict[int, Dict[str, Any]] = {}
    for row in await list_active_workers():
        wid = int(row["worker_id"])
        part = await fetch_go2rtc_streams(worker_base_url(wid))
        out[wid] = part if isinstance(part, dict) else {}
    return out


async def _merged_worker_streams() -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for part in (await _streams_by_worker()).values():
        merged.update(part)
    return merged


async def get_go2rtc_status() -> Dict[str, Any]:
    from app.services.go2rtc_workers import (
        WORKERS_ENABLED,
        list_active_workers,
        worker_base_url,
        worker_config_path,
    )

    built = await build_all_streams_config()
    if WORKERS_ENABLED:
        workers = await list_active_workers()
        worker_rows = []
        running_any = False
        streams_by_worker = await _streams_by_worker()
        for row in workers:
            wid = int(row["worker_id"])
            url = worker_base_url(wid)
            healthy = await is_api_healthy(url)
            running_any = running_any or healthy
            live_count = len(streams_by_worker.get(wid) or {})
            worker_rows.append(
                {
                    "workerId": wid,
                    "pm2Name": row.get("pm2_name"),
                    "baseUrl": url,
                    "apiPort": row.get("api_port"),
                    "rtspPort": row.get("rtsp_port"),
                    "webrtcPort": row.get("webrtc_port"),
                    "assignedCameraCount": row.get("assigned_camera_count"),
                    "maxCameras": row.get("max_cameras", MAX_CAMERAS_PER_WORKER),
                    "running": healthy,
                    "liveStreamCount": live_count,
                    "configPath": str(worker_config_path(wid)),
                }
            )
        return {
            "enabled": GO2RTC_ENABLED,
            "running": running_any,
            "workersEnabled": True,
            "liveProvider": "go2rtc",
            "apiUrl": "/go2rtc",
            "proxyBase": "/go2rtc",
            "binary": str(go2rtc_bin()),
            "binaryFound": go2rtc_bin().is_file(),
            "streamCount": built.get("streamCount", 0) if built.get("ok") else 0,
            "cameraCount": built.get("cameraCount", 0) if built.get("ok") else 0,
            "workers": worker_rows,
            "errors": built.get("errors", []) if built.get("ok") else [built.get("error")],
        }

    healthy = await is_api_healthy()
    return {
        "enabled": GO2RTC_ENABLED,
        "running": healthy,
        "workersEnabled": False,
        "liveProvider": "go2rtc",
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


MAX_CAMERAS_PER_WORKER = int(os.getenv("GO2RTC_MAX_CAMERAS_PER_WORKER", "300"))


async def get_go2rtc_diagnostics() -> Dict[str, Any]:
    """Full status for debug UI: streams, online/offline, consumers."""
    from app.services.stream_issues import (
        ISSUE_LABELS,
        is_confirmed_offline,
        producer_error_text,
        producers_streaming,
        stream_issue_from_row,
        summarize_issues,
    )
    from app.services.stream_health import (
        ensure_stream_health_scan,
        get_stream_health,
        hydrate_stream_health_from_db,
        stream_health_snapshot,
    )

    await hydrate_stream_health_from_db()
    ensure_stream_health_scan()
    built = await build_all_streams_config()
    status = await get_go2rtc_status()
    streams_by_worker = await _streams_by_worker()
    api_streams = await _merged_worker_streams()
    worker_health = {
        int(w["workerId"]): bool(w.get("running"))
        for w in (status.get("workers") or [])
        if w.get("workerId") is not None
    }
    from app.services.go2rtc_workers import normalize_worker_id

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
    unknown_count = 0
    active_consumers = 0

    for cam in built.get("cameras") or []:
        sub = cam["subStream"]
        main = cam["mainStream"]
        cam_worker = normalize_worker_id(cam.get("workerId")) or 1
        worker_streams = streams_by_worker.get(cam_worker) or {}
        worker_running = worker_health.get(cam_worker, False)

        sub_info = worker_streams.get(sub) or {}
        main_info = worker_streams.get(main) or {}

        sub_producers = sub_info.get("producers") or []
        main_producers = main_info.get("producers") or []
        sub_consumers = len(sub_info.get("consumers") or [])
        main_consumers = len(main_info.get("consumers") or [])
        ui_sub = _consumer_counts.get(sub, 0)
        ui_main = _consumer_counts.get(main, 0)

        sub_online = producers_streaming(sub_producers)
        main_online = producers_streaming(main_producers)
        sub_registered = sub in worker_streams
        main_registered = main in worker_streams
        stream_registered = sub_registered or main_registered
        active_consumers += sub_consumers + main_consumers

        sub_stale = max(0, sub_consumers - ui_sub)
        main_stale = max(0, main_consumers - ui_main)

        cam_name = cam["cameraName"]
        cfg_err = config_errors_by_name.get(cam_name)
        health = get_stream_health(cam["cameraId"], cam.get("cameraUid") or "")
        if (
            health
            and not health.get("ok")
            and (sub_online or main_online)
            and not (producer_error_text(sub_producers) or producer_error_text(main_producers))
        ):
            # Only clear probe failures when go2rtc is actually delivering media.
            from app.services.stream_health import clear_stream_health_alarm

            health = clear_stream_health_alarm(cam["cameraId"], cam.get("cameraUid") or "") or {
                "ok": True,
                "alarm": False,
                "suspect": False,
                "checkedAt": health.get("checkedAt"),
            }

        issue_cat, issue_msg = stream_issue_from_row(
            sub_online=sub_online,
            main_online=main_online,
            sub_producers=sub_producers,
            main_producers=main_producers,
            config_error=cfg_err,
            stream_registered=stream_registered,
            worker_running=worker_running,
            worker_id=cam_worker,
            health=health,
        )

        confirmed_offline = is_confirmed_offline(issue_cat)
        health_confirmed = bool(health) and (bool(health.get("ok")) or bool(health.get("alarm")))
        if issue_cat == "online":
            online_count += 1
        elif issue_cat == "unchecked":
            unknown_count += 1
        else:
            offline_count += 1

        display_msg = "" if issue_cat == "online" else (
            issue_msg
            or producer_error_text(sub_producers)
            or producer_error_text(main_producers)
            or ISSUE_LABELS.get(issue_cat, "Not streaming")
        )

        rows.append(
            {
                "cameraId": cam["cameraId"],
                "cameraUid": cam.get("cameraUid") or "",
                "cameraName": cam_name,
                "workerId": cam_worker,
                "workerRunning": worker_running,
                "site": cam.get("site") or "",
                "building": cam.get("building") or "",
                "floor": cam.get("floor") or "",
                "camera_group": cam.get("camera_group") or "",
                "subStream": sub,
                "mainStream": main,
                "subRegistered": sub_registered,
                "mainRegistered": main_registered,
                "streamRegistered": stream_registered,
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
                "issueMessage": display_msg,
                # Future email/WhatsApp: only alert when confirmedOffline is true.
                "confirmedOffline": confirmed_offline,
                "alertEligible": confirmed_offline,
                "healthCheckedAt": health.get("checkedAt") if health else None,
                "healthConfirmed": health_confirmed,
                "healthAlarm": bool(health and health.get("alarm")),
                "healthSuspect": bool(health and health.get("suspect")),
            }
        )

    return {
        **status,
        "configuredStreamCount": built.get("streamCount", 0),
        "camerasOnline": online_count,
        "camerasOffline": offline_count,
        "camerasUnknown": unknown_count,
        "activeConsumers": active_consumers,
        "uiTrackedConsumers": sum(_consumer_counts.values()),
        "streams": rows,
        "configErrors": built.get("errors") or [],
        "missingInGo2rtc": missing_in_go2rtc,
        "staleInGo2rtc": stale_in_go2rtc,
        "issueSummary": summarize_issues(rows),
        "issueLabels": ISSUE_LABELS,
        "locations": _build_location_tree(rows),
        "healthScan": stream_health_snapshot(),
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

    from app.services.go2rtc_workers import WORKERS_ENABLED, sync_all_workers

    if WORKERS_ENABLED:
        return await sync_all_workers()

    built = await write_config_file()
    if not built.get("ok"):
        return {**built, "running": False}

    streams: Dict[str, str] = built.get("streams") or {}
    if await is_api_healthy():
        sync = await sync_streams_to_go2rtc(streams)
        return _running_result(built, reused=True, sync=sync)

    return await start_go2rtc()


async def start_go2rtc_quick() -> Dict[str, Any]:
    """Write config and ensure go2rtc process is up (no API stream push — avoids blocking startup)."""
    if not GO2RTC_ENABLED:
        return {"ok": False, "error": "GO2RTC_ENABLED=false", "running": False}

    built = await write_config_file()
    if not built.get("ok"):
        return {**built, "running": False}

    if await is_api_healthy():
        return {
            "ok": True,
            "running": True,
            "reused": True,
            "streamCount": built.get("streamCount"),
            "cameraCount": built.get("cameraCount"),
        }

    global _proc
    binary = go2rtc_bin()
    if not binary.is_file():
        return {
            "ok": False,
            "running": False,
            "error": f"go2rtc binary not found: {binary}",
        }

    if _proc and _proc.returncode is None:
        return {
            "ok": True,
            "running": True,
            "reused": True,
            "streamCount": built.get("streamCount"),
            "cameraCount": built.get("cameraCount"),
        }

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
            logger.info(
                "[go2rtc] Started pid=%s streams=%s (config loaded from yaml)",
                _proc.pid,
                built.get("streamCount"),
            )
            return {
                "ok": True,
                "running": True,
                "reused": False,
                "streamCount": built.get("streamCount"),
                "cameraCount": built.get("cameraCount"),
            }
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


def schedule_go2rtc_stream_sync(*, reason: str = "startup") -> None:
    """Background push of MongoDB streams into a running go2rtc instance."""

    async def _run() -> None:
        try:
            result = await ensure_go2rtc_streams()
            sync = result.get("sync") or {}
            missing = sync.get("missingCount") or 0
            if result.get("ok") and not missing:
                logger.info(
                    "[go2rtc] Background sync complete (%s): added=%s streams=%s",
                    reason,
                    sync.get("added"),
                    result.get("streamCount"),
                )
            elif missing:
                logger.warning(
                    "[go2rtc] Background sync (%s): %s stream(s) still missing",
                    reason,
                    missing,
                )
            elif not result.get("ok"):
                logger.warning(
                    "[go2rtc] Background sync failed (%s): %s",
                    reason,
                    result.get("error") or sync.get("errors"),
                )
        except Exception as exc:
            logger.warning("[go2rtc] Background sync error (%s): %s", reason, exc)

    asyncio.create_task(_run())


async def start_go2rtc_on_startup() -> None:
    if not GO2RTC_ENABLED:
        logger.info("[go2rtc] Disabled (GO2RTC_ENABLED=false)")
        return

    from app.services.go2rtc_workers import WORKERS_ENABLED, startup_workers

    if WORKERS_ENABLED:
        result = await startup_workers()
        if result.get("ok"):
            logger.info(
                "[go2rtc] Worker fleet ready: %s worker(s)",
                len(result.get("workers") or []),
            )
        else:
            logger.warning("[go2rtc] Worker startup issues: %s", result)
        return

    result = await start_go2rtc_quick()
    if result.get("ok"):
        logger.info(
            "[go2rtc] Process ready api=%s cameras=%s streams=%s (full sync scheduled)",
            GO2RTC_API_URL,
            result.get("cameraCount"),
            result.get("streamCount"),
        )
    else:
        logger.warning("[go2rtc] Not started: %s", result.get("error"))


def get_live_config() -> Dict[str, Any]:
    """Live View always uses Nginx → /media/w{id} → go2rtc (direct media)."""
    from app.services.go2rtc_workers import WORKERS_ENABLED, worker_ports

    media_workers: list[dict] = []
    if WORKERS_ENABLED:
        for wid in range(1, 4):
            api_port, _rtsp, webrtc_port = worker_ports(wid)
            media_workers.append(
                {
                    "workerId": wid,
                    "apiPort": api_port,
                    "webrtcPort": webrtc_port,
                    "mediaPath": f"/media/w{wid}",
                }
            )
    else:
        api_port = GO2RTC_API_PORT
        media_workers.append(
            {
                "workerId": 1,
                "apiPort": api_port,
                "webrtcPort": int(os.getenv("GO2RTC_WEBRTC_PORT", "8555")),
                "mediaPath": "/media/w1",
            }
        )

    return {
        "provider": "go2rtc",
        "go2rtcEnabled": GO2RTC_ENABLED,
        "go2rtcWorkersEnabled": WORKERS_ENABLED,
        # Always true — Python live WS proxy was removed (Task 4B).
        "directMediaEnabled": True,
        "mediaWorkers": media_workers,
    }
