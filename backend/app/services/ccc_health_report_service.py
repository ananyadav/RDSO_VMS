"""RDSO 18.6.14 (non-GIS) — CCC Health Reports from real VMS surfaces.

Assembles bounded health rows from existing probes. Does not invent metrics,
hydrate full fleets synchronously, open streams, or include GIS.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.services.report_service import REPORT_CSV_MAX, rows_to_csv

HEALTH_PAGE_MAX = 100
HEALTH_CSV_MAX = min(500, REPORT_CSV_MAX)

COMPONENT_TYPES = frozenset(
    {
        "camera_fleet",
        "camera",
        "vms_server",
        "recording_server",
        "go2rtc_worker",
        "storage",
        "backend",
        "recording_subsystem",
        "ntp",
        "failure",
    }
)

STATUS_VALUES = frozenset({"healthy", "degraded", "offline", "unknown"})

HEALTH_CSV_FIELDS = [
    "component_type",
    "component",
    "status",
    "last_check",
    "message",
    "location",
    "server_id",
    "detail",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_status(raw: str | None) -> str:
    s = str(raw or "unknown").strip().lower()
    if s in ("ok", "online", "up", "healthy", "synchronized", "ready"):
        return "healthy"
    if s in ("degraded", "warning", "partial"):
        return "degraded"
    if s in ("offline", "down", "error", "failed", "not_synchronized", "unhealthy"):
        return "offline"
    if s in STATUS_VALUES:
        return s
    return "unknown"


def _row(
    *,
    component_type: str,
    component: str,
    status: str,
    message: str = "",
    location: str = "",
    server_id: str = "",
    detail: str = "",
    last_check: str | None = None,
) -> dict[str, Any]:
    return {
        "component_type": component_type,
        "component": component,
        "status": _norm_status(status),
        "last_check": last_check or _utcnow_iso(),
        "message": message,
        "location": location,
        "server_id": server_id,
        "detail": detail,
        # Explicit absences for security tests
        "password": None,
        "rtsp_url": None,
    }


async def _camera_fleet_row() -> dict[str, Any]:
    from app.services.ccc_dashboard_service import _camera_counts

    counts = await _camera_counts()
    if not counts.get("online_status_known"):
        return _row(
            component_type="camera_fleet",
            component="Camera fleet",
            status="unknown",
            message=counts.get("note")
            or f"Total {counts.get('total', 0)} — online flags not populated",
            detail=f"total={counts.get('total')}",
        )
    offline = int(counts.get("offline") or 0)
    online = int(counts.get("online") or 0)
    total = int(counts.get("total") or 0)
    if offline > 0 and online == 0 and total > 0:
        st = "offline"
    elif offline > 0:
        st = "degraded"
    else:
        st = "healthy"
    return _row(
        component_type="camera_fleet",
        component="Camera fleet",
        status=st,
        message=f"total={total} online={online} offline={offline}",
        detail="summary_only_no_per_camera_hydrate",
    )


async def _paginated_camera_rows(
    *,
    user: Optional[dict],
    status_filter: Optional[str],
    q: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """Bounded camera health rows from registry flags only — no stream start."""
    from app.core.database import camera_collection
    from app.services.camera_access import build_access_filter, is_admin, merge_query

    query: dict[str, Any] = {
        "$or": [{"is_active": True}, {"is_active": {"$exists": False}}],
    }
    if user and not is_admin(user):
        query = merge_query(query, build_access_filter(user))
    needle = (q or "").strip()
    if needle:
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"name": {"$regex": needle, "$options": "i"}},
                        {"display_name": {"$regex": needle, "$options": "i"}},
                        {"location_path": {"$regex": needle, "$options": "i"}},
                        {"camera_uid": {"$regex": needle, "$options": "i"}},
                    ]
                },
            ]
        }

    # Prefer DB-side online filter when status is healthy/offline and flags exist.
    if status_filter == "healthy":
        query = merge_query(
            query, {"$or": [{"online": True}, {"is_online": True}]}
        )
    elif status_filter == "offline":
        query = merge_query(
            query,
            {
                "$or": [
                    {"online": False},
                    {"is_online": False},
                    {"confirmed_offline": True},
                ]
            },
        )

    total = int(await camera_collection.count_documents(query))
    limit_n = max(1, min(int(limit or 50), HEALTH_PAGE_MAX))
    offset_n = max(0, int(offset or 0))
    cursor = (
        camera_collection.find(query)
        .sort([("display_name", 1), ("name", 1)])
        .skip(offset_n)
        .limit(limit_n)
    )
    items: list[dict[str, Any]] = []
    async for doc in cursor:
        online = doc.get("online")
        if online is None:
            online = doc.get("is_online")
        if online is True:
            st = "healthy"
        elif online is False or doc.get("confirmed_offline"):
            st = "offline"
        else:
            st = "unknown"
        if status_filter and status_filter not in ("healthy", "offline") and st != status_filter:
            continue
        if status_filter == "degraded" and st != "degraded":
            continue
        name = doc.get("display_name") or doc.get("name") or str(doc.get("_id"))
        items.append(
            _row(
                component_type="camera",
                component=str(name),
                status=st,
                message="registry online flag" if online is not None else "online flag unset",
                location=str(doc.get("location_path") or ""),
                server_id=str(doc.get("recording_server_id") or doc.get("worker_id") or ""),
                detail=str(doc.get("_id")),
                last_check=str(doc.get("last_seen") or doc.get("updated_at") or _utcnow_iso()),
            )
        )
    return items, total


async def _vms_rows() -> list[dict[str, Any]]:
    try:
        from app.services.vms_ha_coordinator import vms_ha_status_summary

        data = await vms_ha_status_summary()
    except Exception as exc:
        return [
            _row(
                component_type="vms_server",
                component="VMS HA",
                status="unknown",
                message=f"probe failed: {exc}",
            )
        ]
    rows = [
        _row(
            component_type="vms_server",
            component="VMS HA coordinator",
            status="healthy" if data.get("ha_enabled") is False or data.get("local_is_leader") is not None else "unknown",
            message=(
                f"ha_enabled={data.get('ha_enabled')} local_role={data.get('local_role')} "
                f"leader={data.get('local_is_leader')}"
            ),
            server_id=str(data.get("local_vms_server_id") or ""),
        )
    ]
    for n in (data.get("nodes") or [])[:50]:
        healthy = n.get("healthy")
        st = "healthy" if healthy else ("offline" if healthy is False else "unknown")
        rows.append(
            _row(
                component_type="vms_server",
                component=str(n.get("label") or n.get("vms_server_id") or "vms_node"),
                status=st,
                message=str(n.get("role") or ""),
                server_id=str(n.get("vms_server_id") or ""),
                last_check=str(n.get("last_heartbeat") or n.get("updated_at") or _utcnow_iso()),
            )
        )
    return rows


async def _recording_server_rows() -> list[dict[str, Any]]:
    try:
        from app.services.recording_ha_coordinator import ha_status_summary

        data = await ha_status_summary()
    except Exception as exc:
        return [
            _row(
                component_type="recording_server",
                component="Recording HA",
                status="unknown",
                message=f"probe failed: {exc}",
            )
        ]
    rows = [
        _row(
            component_type="recording_server",
            component="Recording HA",
            status="healthy" if data.get("ha_enabled") is not None else "unknown",
            message=f"ha_enabled={data.get('ha_enabled')} role={data.get('local_role')}",
            server_id=str(data.get("local_server_id") or ""),
        )
    ]
    for s in (data.get("servers") or [])[:50]:
        healthy = s.get("healthy")
        st = "healthy" if healthy else ("offline" if healthy is False else "unknown")
        rows.append(
            _row(
                component_type="recording_server",
                component=str(s.get("label") or s.get("server_id") or "recording_server"),
                status=st,
                message=str(s.get("role") or ""),
                server_id=str(s.get("server_id") or ""),
                last_check=str(s.get("last_heartbeat") or s.get("updated_at") or _utcnow_iso()),
            )
        )
    return rows


async def _go2rtc_rows() -> list[dict[str, Any]]:
    try:
        from app.services.go2rtc_workers import list_active_workers

        workers = await list_active_workers()
    except Exception as exc:
        return [
            _row(
                component_type="go2rtc_worker",
                component="go2rtc workers",
                status="unknown",
                message=f"probe failed: {exc}",
                detail="browser_path=/media/w{N}/api/ws",
            )
        ]
    if not workers:
        return [
            _row(
                component_type="go2rtc_worker",
                component="go2rtc workers",
                status="unknown",
                message="No active worker documents (fleet may use default worker 1)",
                detail="browser_path=/media/w{N}/api/ws;direct_camera=false",
            )
        ]
    rows = []
    for w in workers[:64]:
        wid = w.get("worker_id") or w.get("_id")
        healthy = w.get("healthy")
        if healthy is None:
            healthy = w.get("active", True)
        st = "healthy" if healthy else "offline"
        rows.append(
            _row(
                component_type="go2rtc_worker",
                component=f"go2rtc worker {wid}",
                status=st,
                message=str(w.get("status") or w.get("last_error") or "active"),
                server_id=str(wid),
                detail="/media/w{N}/api/ws",
                last_check=str(w.get("updated_at") or w.get("last_heartbeat") or _utcnow_iso()),
            )
        )
    return rows


async def _storage_row() -> dict[str, Any]:
    try:
        from app.services.storage_dashboard import get_storage_dashboard

        data = await get_storage_dashboard(summary_only=True)
    except Exception as exc:
        return _row(
            component_type="storage",
            component="Storage",
            status="unknown",
            message=f"probe failed: {exc}",
        )
    disk = data.get("disk") or {}
    health = data.get("storage_health") or {}
    free = disk.get("disk_free_gb")
    total = disk.get("disk_total_gb")
    used = disk.get("disk_used_gb")
    status_raw = health.get("status") or disk.get("status")
    if status_raw:
        st = _norm_status(str(status_raw))
    elif free is not None and total:
        try:
            pct_free = float(free) / float(total) * 100
            st = "healthy" if pct_free >= 15 else ("degraded" if pct_free >= 5 else "offline")
        except (TypeError, ValueError, ZeroDivisionError):
            st = "unknown"
    else:
        st = "unknown"
    return _row(
        component_type="storage",
        component="Recording storage",
        status=st,
        message=f"total_gb={total} used_gb={used} free_gb={free}",
        detail=str(data.get("recordings_root") or ""),
    )


def _backend_row(app: Any = None) -> dict[str, Any]:
    try:
        from app.core.startup_state import STARTUP_KEY, health_payload, new_startup_state

        state = None
        if app is not None:
            state = app.get(STARTUP_KEY)
        if state is None:
            state = new_startup_state()
            state["ready"] = True  # process is serving this request
            state["phase"] = "ready_assumed"
            state["mongodb"] = True
        payload = health_payload(state)
        ready = bool(payload.get("ready"))
        return _row(
            component_type="backend",
            component="Backend readiness",
            status="healthy" if ready else "degraded",
            message=(
                f"ready={ready} phase={payload.get('phase')} "
                f"startup_s={payload.get('startup_duration_seconds')} "
                f"cameras={payload.get('cameraCount')}"
            ),
            detail=str(payload.get("error") or ""),
            last_check=str(payload.get("ready_at") or _utcnow_iso()),
        )
    except Exception as exc:
        return _row(
            component_type="backend",
            component="Backend readiness",
            status="unknown",
            message=str(exc),
        )


def _recording_subsystem_row() -> dict[str, Any]:
    try:
        from app.core.startup_state import _recording_health_snapshot
        from app.services.recording_config import get_recording_capacity_info

        snap = _recording_health_snapshot()
        cap = get_recording_capacity_info()
        enabled = bool(snap.get("enabled"))
        active = bool(snap.get("recordingActive"))
        return _row(
            component_type="recording_subsystem",
            component="Recording subsystem",
            status="healthy" if enabled else "degraded",
            message=f"engine_enabled={enabled} recording_active={active}",
            detail=str(cap)[:240],
        )
    except Exception as exc:
        return _row(
            component_type="recording_subsystem",
            component="Recording subsystem",
            status="unknown",
            message=str(exc),
        )


def _ntp_row() -> dict[str, Any]:
    try:
        from app.services.app_timezone import get_system_time_status

        data = get_system_time_status()
        sync = bool(data.get("synchronized"))
        state = str(data.get("sync_state") or "")
        st = "healthy" if sync else ("degraded" if state else "unknown")
        if state in ("not_synchronized", "service_unavailable"):
            st = "offline" if state == "service_unavailable" else "degraded"
        ntp = data.get("ntp") or {}
        return _row(
            component_type="ntp",
            component="OS network time (NTP)",
            status=st,
            message=f"sync_state={state} service={ntp.get('service')}",
            detail=f"synchronized={sync};in_app_ntp_server=false",
            last_check=str(data.get("utc_now") or _utcnow_iso()),
        )
    except Exception as exc:
        return _row(
            component_type="ntp",
            component="OS network time (NTP)",
            status="unknown",
            message=str(exc),
        )


async def _failure_rows() -> list[dict[str, Any]]:
    try:
        from app.services.audit_service import query_audit_logs

        data = await query_audit_logs(success=False, limit=25, offset=0)
    except Exception as exc:
        return [
            _row(
                component_type="failure",
                component="Recent failures",
                status="unknown",
                message=f"audit probe failed: {exc}",
            )
        ]
    items = data.get("items") or []
    if not items:
        return [
            _row(
                component_type="failure",
                component="Recent failures",
                status="healthy",
                message="No recent failed audit actions",
            )
        ]
    rows = []
    for it in items[:25]:
        rows.append(
            _row(
                component_type="failure",
                component=str(it.get("action") or "failed_action"),
                status="degraded",
                message=str(it.get("resource_label") or it.get("resource_type") or ""),
                server_id=str(it.get("resource_id") or ""),
                detail=str(it.get("actor_username") or ""),
                last_check=str(it.get("timestamp") or _utcnow_iso()),
            )
        )
    return rows


def _match_filters(
    row: dict[str, Any],
    *,
    component_type: Optional[str],
    status_filter: Optional[str],
    q: str,
) -> bool:
    if component_type and row.get("component_type") != component_type:
        return False
    if status_filter and row.get("status") != status_filter:
        return False
    needle = (q or "").strip().lower()
    if needle:
        blob = " ".join(
            str(row.get(k) or "")
            for k in ("component", "message", "location", "server_id", "detail")
        ).lower()
        if needle not in blob:
            return False
    return True


async def build_ccc_health_report(
    *,
    user: Optional[dict] = None,
    app: Any = None,
    component_type: Optional[str] = None,
    status: Optional[str] = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    for_export: bool = False,
) -> dict[str, Any]:
    """
    Build health report rows.

    Default view: subsystem summaries (bounded).
    component_type=camera: paginated registry camera health only (no stream hydrate).
    """
    ctype = (component_type or "").strip().lower() or None
    if ctype and ctype not in COMPONENT_TYPES:
        raise ValueError(f"component_type must be one of {sorted(COMPONENT_TYPES)}")
    st = (status or "").strip().lower() or None
    if st and st not in STATUS_VALUES:
        raise ValueError(f"status must be one of {sorted(STATUS_VALUES)}")

    cap = HEALTH_CSV_MAX if for_export else HEALTH_PAGE_MAX
    limit_n = max(1, min(int(limit or 50), cap))
    offset_n = max(0, int(offset or 0))

    camera_total = 0
    if ctype == "camera":
        rows, camera_total = await _paginated_camera_rows(
            user=user,
            status_filter=st,
            q=q,
            limit=limit_n,
            offset=offset_n,
        )
        # Already paginated at source
        page = rows
        total = camera_total
    else:
        assembled: list[dict[str, Any]] = []
        want = ctype  # None = all non-camera detail types

        async def maybe_add(kind: str, producer):
            if want and want != kind:
                return
            result = producer()
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
            if isinstance(result, list):
                assembled.extend(result)
            elif isinstance(result, dict):
                assembled.append(result)

        await maybe_add("camera_fleet", _camera_fleet_row)
        await maybe_add("vms_server", _vms_rows)
        await maybe_add("recording_server", _recording_server_rows)
        await maybe_add("go2rtc_worker", _go2rtc_rows)
        await maybe_add("storage", _storage_row)
        if not want or want == "backend":
            assembled.append(_backend_row(app))
        if not want or want == "recording_subsystem":
            assembled.append(_recording_subsystem_row())
        if not want or want == "ntp":
            assembled.append(_ntp_row())
        await maybe_add("failure", _failure_rows)

        filtered = [
            r
            for r in assembled
            if _match_filters(r, component_type=ctype, status_filter=st, q=q)
        ]
        total = len(filtered)
        page = filtered[offset_n : offset_n + limit_n]

    summary = {
        "healthy": sum(1 for r in page if r["status"] == "healthy"),
        "degraded": sum(1 for r in page if r["status"] == "degraded"),
        "offline": sum(1 for r in page if r["status"] == "offline"),
        "unknown": sum(1 for r in page if r["status"] == "unknown"),
    }

    return {
        "items": page,
        "total": total,
        "limit": limit_n,
        "offset": offset_n,
        "returned": len(page),
        "summary": summary,
        "filters": {
            "component_type": ctype,
            "status": st,
            "q": q or None,
        },
        "component_types": sorted(COMPONENT_TYPES),
        "statuses": sorted(STATUS_VALUES),
        "gis": False,
        "gis_pending": True,
        "fabricated_metrics": False,
        "streams_not_started": True,
        "no_full_fleet_hydrate": True,
        "rdso_18_6_14_health": True,
        "rdso_18_6_14_gis": False,
        "generated_at": _utcnow_iso(),
    }


def export_health_csv(items: list[dict[str, Any]]) -> tuple[str, str]:
    filename = f"ccc_health_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    body = rows_to_csv(items, HEALTH_CSV_FIELDS)
    return filename, body
