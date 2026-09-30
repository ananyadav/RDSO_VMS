"""RDSO 18.3.13 — evidence integrity (SHA-256 manifests).

Completed recording sessions get a sealed evidence_manifest.json listing each
protected media file and its SHA-256 digest. Verification re-hashes files and
compares against the sealed manifest — hashes are never silently regenerated
after a file changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "evidence_manifest.json"
MANIFEST_SCHEMA = "rdso_18_3_13_evidence_manifest_v1"
ALGORITHM = "SHA-256"

VERIFY_VALID = "valid"
VERIFY_MODIFIED = "modified"
VERIFY_MISSING = "missing"
VERIFY_NO_MANIFEST = "no_manifest"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def list_protected_files(session_dir: Path) -> list[Path]:
    """Media files covered by the evidence manifest (playlist + segments)."""
    if not session_dir.is_dir():
        return []
    files: list[Path] = []
    playlist = session_dir / "index.m3u8"
    if playlist.is_file():
        files.append(playlist)
    segments = sorted(session_dir.glob("seg_*.ts"))
    if not segments:
        segments = sorted(p for p in session_dir.glob("*.ts") if p.is_file())
    files.extend(segments)
    # Include pre_alarm media if present (alarm pre-roll)
    pre = session_dir / "pre_alarm"
    if pre.is_dir():
        for p in sorted(pre.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".ts", ".m3u8", ".mp4", ".mkv"}:
                files.append(p)
    return files


def seal_vod_playlist_on_disk(session_dir: Path) -> bool:
    """Append #EXT-X-ENDLIST to on-disk playlist once when finalizing."""
    playlist = session_dir / "index.m3u8"
    if not playlist.is_file():
        return False
    try:
        text = playlist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "#EXT-X-ENDLIST" in text:
        return False
    if not text.endswith("\n"):
        text += "\n"
    text += "#EXT-X-ENDLIST\n"
    playlist.write_text(text, encoding="utf-8")
    return True


def manifest_path_for(session_dir: Path) -> Path:
    return session_dir / MANIFEST_FILENAME


def load_manifest(session_dir: Path) -> Optional[dict[str, Any]]:
    path = manifest_path_for(session_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[EVIDENCE] Cannot read manifest %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def build_manifest_payload(
    session_dir: Path,
    *,
    session: dict[str, Any],
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    files_meta: list[dict[str, Any]] = []
    for path in list_protected_files(session_dir):
        rel = path.relative_to(session_dir).as_posix()
        try:
            digest = sha256_file(path)
            size = path.stat().st_size
        except OSError as exc:
            raise RuntimeError(f"Cannot hash {rel}: {exc}") from exc
        files_meta.append({"path": rel, "sha256": digest, "size": size})

    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "algorithm": ALGORITHM,
        "created_at": created_at or _utcnow_iso(),
        "camera_id": str(session.get("camera_id") or ""),
        "camera_uid": str(session.get("camera_uid") or ""),
        "camera_name": str(session.get("camera_name") or ""),
        "ip_address": str(session.get("ip_address") or ""),
        "session_id": str(session.get("id") or session.get("session_id") or ""),
        "started_at": session.get("started_at"),
        "stopped_at": session.get("stopped_at"),
        "source": session.get("source") or "vms",
        "storage_path": session.get("storage_path") or session.get("file_path"),
        "files": files_meta,
        "file_count": len(files_meta),
    }
    # Self-integrity over canonical JSON without this field
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["manifest_sha256"] = sha256_bytes(canonical)
    return payload


def write_manifest(session_dir: Path, payload: dict[str, Any]) -> Path:
    path = manifest_path_for(session_dir)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def create_evidence_manifest(
    session_dir: Path,
    session: dict[str, Any],
    *,
    force: bool = False,
    seal_playlist: bool = True,
) -> dict[str, Any]:
    """Create (or refuse to overwrite) an evidence manifest for a session directory.

    force=False (default): if a manifest already exists, return it unchanged.
    Never regenerates digests after media has been sealed unless force=True
    (explicit admin action only).
    """
    session_dir = Path(session_dir)
    existing = load_manifest(session_dir)
    if existing is not None and not force:
        return {
            "ok": True,
            "created": False,
            "reason": "manifest_already_exists",
            "manifest": existing,
            "manifest_path": str(manifest_path_for(session_dir)),
        }

    if not session_dir.is_dir():
        return {
            "ok": False,
            "created": False,
            "reason": "session_dir_missing",
            "error": f"Session directory missing: {session_dir}",
        }

    if seal_playlist:
        seal_vod_playlist_on_disk(session_dir)

    protected = list_protected_files(session_dir)
    if not protected:
        return {
            "ok": False,
            "created": False,
            "reason": "no_media_files",
            "error": "No playlist/segments to protect",
        }

    payload = build_manifest_payload(session_dir, session=session)
    write_manifest(session_dir, payload)
    return {
        "ok": True,
        "created": True,
        "reason": "created" if not existing else "regenerated",
        "manifest": payload,
        "manifest_path": str(manifest_path_for(session_dir)),
    }


def verify_evidence_manifest(session_dir: Path) -> dict[str, Any]:
    """Compare on-disk files to the sealed manifest. Does not rewrite the manifest."""
    session_dir = Path(session_dir)
    manifest = load_manifest(session_dir)
    if manifest is None:
        return {
            "status": VERIFY_NO_MANIFEST,
            "valid": False,
            "algorithm": ALGORITHM,
            "message": "No evidence manifest present for this session",
            "files": [],
            "checked_at": _utcnow_iso(),
        }

    results: list[dict[str, Any]] = []
    modified = 0
    missing = 0
    ok_count = 0
    for entry in manifest.get("files") or []:
        rel = str(entry.get("path") or "")
        expected = str(entry.get("sha256") or "")
        expected_size = entry.get("size")
        path = session_dir / rel
        row: dict[str, Any] = {
            "path": rel,
            "expected_sha256": expected,
            "expected_size": expected_size,
        }
        if not path.is_file():
            row["status"] = VERIFY_MISSING
            row["actual_sha256"] = None
            missing += 1
            results.append(row)
            continue
        try:
            actual = sha256_file(path)
            actual_size = path.stat().st_size
        except OSError as exc:
            row["status"] = VERIFY_MISSING
            row["error"] = str(exc)
            missing += 1
            results.append(row)
            continue
        row["actual_sha256"] = actual
        row["actual_size"] = actual_size
        if actual.lower() != expected.lower():
            row["status"] = VERIFY_MODIFIED
            modified += 1
        else:
            row["status"] = VERIFY_VALID
            ok_count += 1
        results.append(row)

    if missing and not modified and ok_count == 0:
        overall = VERIFY_MISSING
        message = "Protected media files are missing"
    elif missing and not modified:
        overall = VERIFY_MISSING
        message = f"{missing} protected file(s) missing"
    elif modified:
        overall = VERIFY_MODIFIED
        message = f"{modified} protected file(s) modified"
    else:
        overall = VERIFY_VALID
        message = "All protected files match the evidence manifest"

    return {
        "status": overall,
        "valid": overall == VERIFY_VALID,
        "algorithm": manifest.get("algorithm") or ALGORITHM,
        "schema": manifest.get("schema"),
        "session_id": manifest.get("session_id"),
        "camera_id": manifest.get("camera_id"),
        "camera_uid": manifest.get("camera_uid"),
        "created_at": manifest.get("created_at"),
        "checked_at": _utcnow_iso(),
        "message": message,
        "file_count": len(results),
        "valid_count": ok_count,
        "modified_count": modified,
        "missing_count": missing,
        "files": results,
        "manifest_path": str(manifest_path_for(session_dir)),
    }


async def attach_evidence_to_session(
    session_id: str,
    session_dir: Path,
    session: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Create manifest (if needed) and persist summary fields on the Mongo session."""
    from app.core.database import update_recording_session

    result = create_evidence_manifest(session_dir, session, force=force)
    if not result.get("ok"):
        return result
    manifest = result.get("manifest") or {}
    await update_recording_session(
        session_id,
        {
            "evidence_integrity": {
                "algorithm": ALGORITHM,
                "schema": MANIFEST_SCHEMA,
                "manifest_file": MANIFEST_FILENAME,
                "created_at": manifest.get("created_at"),
                "file_count": manifest.get("file_count"),
                "manifest_sha256": manifest.get("manifest_sha256"),
                "sealed": True,
            }
        },
    )
    return result


def export_file_integrity(path: Path, *, role: str = "exported_clip") -> dict[str, Any]:
    """Integrity block for an offline exported file."""
    digest = sha256_file(path)
    return {
        "role": role,
        "filename": path.name,
        "sha256": digest,
        "size": path.stat().st_size,
        "algorithm": ALGORITHM,
    }
