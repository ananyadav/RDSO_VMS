"""Recording volume health for RDSO 18.1.12 / 18.3.10.

VMS stores recordings on a configurable OS filesystem path. Local disks and
OS-mounted DAS / NAS (NFS/SMB) / SAN (iSCSI) volumes are supported the same way:
the host mounts them; the VMS uses normal file I/O. No vendor NAS/SAN protocols
are implemented in-process.

Status model (18.3.10): Online | Low Space | Critical | Unavailable
(+ Read Only for mount present but not writable).
Configured storage is never silently replaced with another disk volume.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATUS_ONLINE = "online"
STATUS_UNAVAILABLE = "unavailable"
STATUS_READ_ONLY = "read_only"
STATUS_LOW_SPACE = "low_space"
STATUS_CRITICAL = "critical"

STATUS_LABELS = {
    STATUS_ONLINE: "Online",
    STATUS_UNAVAILABLE: "Unavailable",
    STATUS_READ_ONLY: "Read Only",
    STATUS_LOW_SPACE: "Low Space",
    STATUS_CRITICAL: "Critical",
}


class StorageVolumeError(RuntimeError):
    """Base error when recording storage cannot be used."""

    def __init__(self, message: str, *, status: str, probe: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.probe = probe or {}


class StorageUnavailableError(StorageVolumeError):
    def __init__(self, message: str, *, probe: Optional[dict] = None):
        super().__init__(message, status=STATUS_UNAVAILABLE, probe=probe)


class StorageReadOnlyError(StorageVolumeError):
    def __init__(self, message: str, *, probe: Optional[dict] = None):
        super().__init__(message, status=STATUS_READ_ONLY, probe=probe)


class StorageInsufficientSpaceError(StorageVolumeError):
    def __init__(self, message: str, *, probe: Optional[dict] = None):
        super().__init__(message, status=STATUS_CRITICAL, probe=probe)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def min_free_bytes() -> int:
    """Hard floor: refuse new recordings below this free space (default 1 GiB)."""
    gb = _env_float("RECORDING_MIN_FREE_GB", 1.0)
    return max(0, int(gb * 1024**3))


def low_space_free_percent() -> float:
    """Warn / Low Space when free percent is at or below this (default 20)."""
    return max(1.0, min(90.0, _env_float("RECORDING_LOW_SPACE_PERCENT", 20.0)))


def critical_free_percent() -> float:
    """Refuse new recordings when free percent is at or below this (default 5)."""
    return max(0.5, min(50.0, _env_float("RECORDING_CRITICAL_FREE_PERCENT", 5.0)))


def _probe_writable(path: Path) -> tuple[bool, Optional[str]]:
    """Create and delete a tiny probe file under path."""
    probe_name = f".vms_write_probe_{uuid.uuid4().hex[:12]}"
    probe_path = path / probe_name
    try:
        with open(probe_path, "wb") as fh:
            fh.write(b"ok")
            fh.flush()
            os.fsync(fh.fileno())
        probe_path.unlink(missing_ok=True)
        return True, None
    except OSError as exc:
        try:
            probe_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False, str(exc)


def _disk_usage_bytes(path: Path) -> tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    """Return (total, used, free, error). Never substitutes another volume."""
    try:
        import psutil

        usage = psutil.disk_usage(str(path))
        return int(usage.total), int(usage.used), int(usage.free), None
    except Exception as exc:
        # Fallback to statvfs / shutil without changing the target path.
        try:
            usage = os.statvfs(path)  # type: ignore[attr-defined]
            total = int(usage.f_frsize * usage.f_blocks)
            free = int(usage.f_frsize * usage.f_bavail)
            used = total - free
            return total, used, free, None
        except Exception:
            try:
                import shutil

                total, used, free = shutil.disk_usage(path)
                return int(total), int(used), int(free), None
            except Exception as exc2:
                return None, None, None, f"{exc}; {exc2}"


def probe_storage_path(path: Path | str, *, create_if_missing: bool = False) -> dict[str, Any]:
    """Probe a recordings root path for RDSO 18.1.12 status."""
    raw = str(path).strip()
    result: dict[str, Any] = {
        "path": raw,
        "storage_model": "os_filesystem",
        "storage_model_note": (
            "Local disk or OS-mounted DAS/NAS/SAN filesystem. "
            "VMS does not speak vendor NAS/SAN protocols."
        ),
        "exists": False,
        "is_dir": False,
        "writable": False,
        "read_only": False,
        "available": False,
        "total_bytes": None,
        "used_bytes": None,
        "free_bytes": None,
        "disk_total_gb": 0.0,
        "disk_used_gb": 0.0,
        "disk_free_gb": 0.0,
        "disk_free_percent": 0.0,
        "disk_percent": 0.0,
        "status": STATUS_UNAVAILABLE,
        "status_label": STATUS_LABELS[STATUS_UNAVAILABLE],
        "status_level": "red",
        "allow_recording": False,
        "error": None,
        "probed_at": time.time(),
    }
    if not raw:
        result["error"] = "Recording folder path is empty"
        return result

    try:
        p = Path(raw)
        # Avoid resolve() on missing UNC/mounts that may hang; expanduser only.
        p = p.expanduser()
        if not p.is_absolute():
            p = p.resolve()
    except OSError as exc:
        result["error"] = f"Invalid path: {exc}"
        return result

    result["path"] = str(p)

    try:
        exists = p.exists()
    except OSError as exc:
        result["error"] = f"Path inaccessible (mount unavailable?): {exc}"
        return result

    if not exists:
        if create_if_missing:
            try:
                p.mkdir(parents=True, exist_ok=True)
                exists = p.exists()
            except OSError as exc:
                result["error"] = f"Path does not exist and could not be created: {exc}"
                return result
        if not exists:
            result["error"] = "Path does not exist (unavailable mount or missing folder)"
            return result

    result["exists"] = True
    try:
        is_dir = p.is_dir()
    except OSError as exc:
        result["error"] = f"Cannot stat directory: {exc}"
        return result

    if not is_dir:
        result["error"] = "Path exists but is not a directory"
        return result
    result["is_dir"] = True

    total, used, free, du_err = _disk_usage_bytes(p)
    if du_err and total is None:
        # Directory visible but volume stats failed — treat as unavailable, do not
        # report another drive's free space.
        result["error"] = f"Volume stats unavailable for configured path (no fallback): {du_err}"
        return result

    if total is not None and free is not None and used is not None:
        result["total_bytes"] = total
        result["used_bytes"] = used
        result["free_bytes"] = free
        result["disk_total_gb"] = round(total / 1024**3, 2)
        result["disk_used_gb"] = round(used / 1024**3, 2)
        result["disk_free_gb"] = round(free / 1024**3, 2)
        free_pct = round(free / total * 100, 1) if total else 0.0
        result["disk_free_percent"] = free_pct
        result["disk_percent"] = round(100.0 - free_pct, 1) if total else 0.0

    writable, write_err = _probe_writable(p)
    result["writable"] = writable
    if not writable:
        result["read_only"] = True
        result["available"] = True  # mount present but not writable
        result["status"] = STATUS_READ_ONLY
        result["status_label"] = STATUS_LABELS[STATUS_READ_ONLY]
        result["status_level"] = "red"
        result["allow_recording"] = False
        result["error"] = write_err or "Recording folder is not writable"
        return result

    result["available"] = True
    free_b = int(result["free_bytes"] or 0)
    free_pct = float(result["disk_free_percent"] or 0.0)
    min_free = min_free_bytes()
    low_pct = low_space_free_percent()
    crit_pct = critical_free_percent()

    insufficient = (min_free > 0 and free_b < min_free) or (free_pct <= crit_pct and result["total_bytes"])
    low = free_pct <= low_pct if result["total_bytes"] else False

    if insufficient:
        result["status"] = STATUS_CRITICAL
        result["status_label"] = STATUS_LABELS[STATUS_CRITICAL]
        result["status_level"] = "red"
        result["allow_recording"] = False
        result["error"] = (
            f"Critical free space on configured storage "
            f"({result['disk_free_gb']} GB free; min {min_free / 1024**3:.2f} GB / "
            f"critical ≤{crit_pct}%)"
        )
        return result

    if low:
        result["status"] = STATUS_LOW_SPACE
        result["status_label"] = STATUS_LABELS[STATUS_LOW_SPACE]
        result["status_level"] = "yellow"
        result["allow_recording"] = True
        result["error"] = None
        return result

    result["status"] = STATUS_ONLINE
    result["status_label"] = STATUS_LABELS[STATUS_ONLINE]
    result["status_level"] = "green"
    result["allow_recording"] = True
    result["error"] = None
    return result


def probe_recordings_storage(*, create_if_missing: bool = False) -> dict[str, Any]:
    from app.services.storage_settings_store import get_effective_recordings_dir

    return probe_storage_path(get_effective_recordings_dir(), create_if_missing=create_if_missing)


def assert_storage_ready_for_recording(*, create_if_missing: bool = True) -> dict[str, Any]:
    """Raise if configured recordings storage cannot accept a new recording."""
    probe = probe_recordings_storage(create_if_missing=create_if_missing)
    if probe.get("allow_recording"):
        return probe
    status = probe.get("status") or STATUS_UNAVAILABLE
    msg = probe.get("error") or f"Recording storage not ready ({status})"
    if status == STATUS_READ_ONLY:
        raise StorageReadOnlyError(msg, probe=probe)
    if status in (STATUS_CRITICAL, STATUS_LOW_SPACE) and not probe.get("allow_recording"):
        raise StorageInsufficientSpaceError(msg, probe=probe)
    raise StorageUnavailableError(msg, probe=probe)


def disk_payload_from_probe(probe: dict[str, Any]) -> dict[str, Any]:
    """Shape used by /api/storage/dashboard disk section (RDSO 18.3.10)."""
    total = probe.get("disk_total_gb") or 0.0
    used = probe.get("disk_used_gb") or 0.0
    free = probe.get("disk_free_gb") or 0.0
    free_pct = probe.get("disk_free_percent") or 0.0
    used_pct = probe.get("disk_percent") or 0.0
    return {
        "disk_path": probe.get("path") or "",
        "disk_total_gb": total,
        "disk_used_gb": used,
        "disk_free_gb": free,
        "disk_free_percent": free_pct,
        "disk_percent": used_pct,
        # Explicit 18.3.10 capacity fields
        "total_gb": total,
        "used_gb": used,
        "free_gb": free,
        "percent_used": used_pct,
        "percent_free": free_pct,
        "total_bytes": probe.get("total_bytes"),
        "used_bytes": probe.get("used_bytes"),
        "free_bytes": probe.get("free_bytes"),
        "status": probe.get("status"),
        "status_label": probe.get("status_label"),
        "status_level": probe.get("status_level"),
        # Legacy aliases kept for older UI
        "legacy_health_label": {
            "green": "Healthy",
            "yellow": "Low",
            "red": "Critical",
        }.get(str(probe.get("status_level") or "red"), "Critical"),
        "writable": bool(probe.get("writable")),
        "read_only": bool(probe.get("read_only")),
        "available": bool(probe.get("available")),
        "allow_recording": bool(probe.get("allow_recording")),
        "storage_model": probe.get("storage_model"),
        "storage_model_note": probe.get("storage_model_note"),
        "error": probe.get("error"),
        "rdso_18_3_10": True,
    }
