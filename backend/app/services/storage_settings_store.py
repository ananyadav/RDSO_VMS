"""Persisted storage settings (retention, recordings folder) — MongoDB + runtime apply."""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.database import database

logger = logging.getLogger(__name__)

# Windows absolute path embedded inside a corrupted cross-platform string.
_WIN_ABS_RE = re.compile(r"([A-Za-z]:[\\/].+)")

_settings_collection = database.get_collection("system_settings")
_SETTINGS_ID = "storage"

_runtime_retention_days: Optional[float] = None
_runtime_recordings_dir: Optional[str] = None


def _env_default_retention_days() -> float:
    from app.services.recording_config import _env_retention_days

    return _env_retention_days()


def _env_default_recordings_dir() -> str:
    import os

    if os.getenv("RECORDINGS_DIR"):
        return str(Path(os.getenv("RECORDINGS_DIR")).resolve())
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    return str((project_root / "Recordings").resolve())


def get_effective_retention_days() -> float:
    if _runtime_retention_days is not None:
        return _runtime_retention_days
    return _env_default_retention_days()


def get_effective_retention_seconds() -> float:
    return get_effective_retention_days() * 86400


def get_effective_recordings_dir() -> Path:
    raw = _runtime_recordings_dir or _env_default_recordings_dir()
    return normalize_recordings_path(raw)


def _is_windows_abs_path(text: str) -> bool:
    return bool(_WIN_ABS_RE.search(text)) or (len(text) >= 2 and text[1] == ":")


def _is_posix_abs_path(text: str) -> bool:
    return text.startswith("/") and not _is_windows_abs_path(text)


def _path_usable_on_this_host(path_str: str) -> bool:
    """True if the stored path is appropriate for the current OS."""
    text = str(path_str).strip()
    if not text:
        return False
    if sys.platform == "win32":
        # Reject Linux absolute paths on Windows (shared Atlas / Linux deploy path).
        if _is_posix_abs_path(text):
            return False
        return True
    # Linux: reject pure Windows drive paths.
    if _is_windows_abs_path(text) and not text.startswith("/"):
        return False
    return True


def _repair_mixed_recordings_path(raw: str) -> str:
    """Fix paths that accidentally concatenated POSIX + Windows segments.

    On Linux, a pure Windows drive path cannot be used — returns "" so callers
    can fall back to the env/project default.
    """
    text = str(raw).strip().strip('"').strip("'")
    if not text:
        return text

    win_match = _WIN_ABS_RE.search(text)
    has_posix = text.startswith("/")
    has_windows = _is_windows_abs_path(text)

    if has_posix and has_windows:
        if os.name == "nt" and win_match:
            return win_match.group(1)
        # Linux host: keep the POSIX portion before any Windows drive letter.
        return re.split(r"[A-Za-z]:", text, maxsplit=1)[0].rstrip("/\\")

    # Pure Windows path stored in Mongo while running on Linux (common after
    # developing on Windows and deploying to the production Linux host).
    if sys.platform != "win32" and has_windows and not has_posix:
        return ""

    # Pure Linux path while running on Windows — not usable here.
    if sys.platform == "win32" and _is_posix_abs_path(text):
        return ""

    return text


def normalize_recordings_path(path: str | Path) -> Path:
    """Resolve user input to one absolute folder path (never append to the old folder)."""
    repaired = _repair_mixed_recordings_path(str(path))
    if not repaired:
        # Empty after repair (wrong-OS path) → project/env default.
        repaired = _env_default_recordings_dir()
    if not repaired:
        raise ValueError("Recording folder path is required")

    # Use sys.platform (not os.name): pathlib.Path picks WindowsPath when
    # os.name == "nt", which raises NotImplementedError on Linux even if a
    # caller temporarily patches os.name.
    if sys.platform == "win32" and len(repaired) >= 2 and repaired[1] == ":":
        resolved = Path(repaired).resolve()
    else:
        resolved = Path(repaired).expanduser().resolve()

    return resolved


def apply_retention_days(days: float) -> None:
    global _runtime_retention_days
    _runtime_retention_days = float(days)
    logger.info("[STORAGE] Retention set to %.1f day(s)", days)


def apply_recordings_dir(path: str | Path) -> Path:
    global _runtime_recordings_dir
    resolved = normalize_recordings_path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    _runtime_recordings_dir = str(resolved)

    import app.services.video_recording as vr

    vr.RECORDINGS_DIR = resolved
    logger.info("[STORAGE] Recordings folder set to %s", resolved)
    return resolved


async def load_storage_settings() -> None:
    doc = await _settings_collection.find_one({"_id": _SETTINGS_ID}) or {}
    if doc.get("retention_days") is not None:
        try:
            apply_retention_days(float(doc["retention_days"]))
        except (TypeError, ValueError) as exc:
            logger.warning("[STORAGE] Invalid stored retention_days: %s", exc)
    if doc.get("recordings_dir"):
        try:
            stored = str(doc["recordings_dir"])
            persist_fix = False
            if not _path_usable_on_this_host(stored):
                repaired = _env_default_recordings_dir()
                logger.warning(
                    "[STORAGE] Ignoring wrong-OS recordings_dir for this host: %s -> %s",
                    stored,
                    repaired,
                )
                # Only heal Mongo when running on Linux (production). Keep the
                # Linux path in Atlas if a Windows laptop loads settings.
                persist_fix = sys.platform != "win32"
            else:
                repaired = _repair_mixed_recordings_path(stored) or stored
                if repaired != stored:
                    persist_fix = True
            resolved = apply_recordings_dir(repaired)
            if persist_fix and str(resolved) != stored:
                logger.warning(
                    "[STORAGE] Persisting repaired recordings_dir in MongoDB: %s -> %s",
                    stored,
                    resolved,
                )
                await _settings_collection.update_one(
                    {"_id": _SETTINGS_ID},
                    {
                        "$set": {
                            "recordings_dir": str(resolved),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    },
                )
        except (OSError, ValueError) as exc:
            logger.warning("[STORAGE] Could not apply recordings_dir: %s", exc)


def get_storage_settings_public() -> dict:
    days = get_effective_retention_days()
    folder = get_effective_recordings_dir()
    return {
        "retention_days": round(days, 3),
        "retention_seconds": int(days * 86400),
        "retention_label": f"{days:g} day(s)",
        "recordings_dir": str(folder),
        "recordings_dir_editable": True,
        "retention_editable": True,
    }


async def update_storage_settings(
    *,
    retention_days: Optional[float] = None,
    recordings_dir: Optional[str] = None,
) -> dict:
    patch: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}

    if retention_days is not None:
        days = float(retention_days)
        if days < 1 or days > 3650:
            raise ValueError("Retention must be between 1 and 3650 days")
        apply_retention_days(days)
        patch["retention_days"] = days

    if recordings_dir is not None:
        folder = _repair_mixed_recordings_path(str(recordings_dir).strip())
        if not folder:
            if sys.platform != "win32" and _is_windows_abs_path(str(recordings_dir)):
                raise ValueError(
                    "Windows recording path is not valid on this Linux server. "
                    "Use a Linux path such as /home/vms/cctv_ananya/CCTV/Recordings"
                )
            raise ValueError("Recording folder path is required")
        resolved = apply_recordings_dir(folder)
        if not resolved.is_dir():
            raise ValueError("Recording folder does not exist and could not be created")
        patch["recordings_dir"] = str(resolved)

    if len(patch) <= 1:
        raise ValueError("No settings to update")

    await _settings_collection.update_one(
        {"_id": _SETTINGS_ID},
        {"$set": patch},
        upsert=True,
    )
    return get_storage_settings_public()
