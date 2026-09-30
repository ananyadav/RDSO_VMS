"""Persistent external VMS integration configuration (RDSO 18.6.17.3)."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import database
from app.services.audit_service import sanitize_metadata
from app.services.ccc_device_service import hash_integration_secret, verify_integration_secret
from app.services.ccc_vms_credential_crypto import encrypt_credential

integrations_collection = database.get_collection("ccc_vms_integrations")

VENDOR_TYPES = frozenset({"generic_rest"})
AUTH_TYPES = frozenset({"bearer", "basic", "api_key_header"})
DEFAULT_CAPABILITIES = {
    "live": False,
    "playback": False,
    "events": False,
    "health": True,
}
DEFAULT_ENDPOINTS = {
    "cameras": "/api/cameras",
    "live_media": "/api/cameras/{camera_id}/client-media",
    "playback_search": "/api/playback/search",
    "health": "/api/health",
}

_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


class VmsIntegrationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def validate_source_id(source_id: str) -> str:
    sid = (source_id or "").strip().lower()
    if sid == "local":
        raise VmsIntegrationError("source_id 'local' is reserved for LocalVmsSource")
    if not _SOURCE_ID_RE.match(sid):
        raise VmsIntegrationError(
            "source_id must be 2–63 chars: lowercase letter first, then a-z0-9_-"
        )
    return sid


def integration_to_public(doc: dict, *, include_secret_once: str | None = None) -> dict[str, Any]:
    caps = dict(DEFAULT_CAPABILITIES)
    if isinstance(doc.get("capabilities"), dict):
        for k, v in doc["capabilities"].items():
            if k in caps:
                caps[k] = bool(v)
    endpoints = dict(DEFAULT_ENDPOINTS)
    if isinstance(doc.get("endpoints"), dict):
        for k, v in doc["endpoints"].items():
            if k in endpoints and v:
                endpoints[k] = str(v)
    return {
        "source_id": doc.get("source_id") or "",
        "label": doc.get("label") or "",
        "vendor_type": doc.get("vendor_type") or "generic_rest",
        "base_url": doc.get("base_url") or "",
        "enabled": bool(doc.get("enabled", False)),
        "capabilities": caps,
        "endpoints": endpoints,
        "auth_type": doc.get("auth_type") or "bearer",
        "auth_header": doc.get("auth_header") or "Authorization",
        "tls_verify": bool(doc.get("tls_verify", True)),
        "timeout_seconds": int(doc.get("timeout_seconds") or 10),
        "health": doc.get("health") or "unknown",
        "last_seen": _iso(doc.get("last_seen")),
        "last_success": _iso(doc.get("last_success")),
        "last_error": doc.get("last_error") or "",
        "last_test_at": _iso(doc.get("last_test_at")),
        "has_credentials": bool(doc.get("credential_encrypted")),
        "has_ingest_secret": bool(doc.get("ingest_secret_hash")),
        "fake_vendor": False,
        "named_vendor_sdk": False,
        "adapter_kind": "generic_rest",
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "metadata": sanitize_metadata(
            doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        ),
        **(
            {
                "integration_secret": include_secret_once,
                "integration_secret_note": "Shown once for alert webhook auth",
            }
            if include_secret_once
            else {}
        ),
    }


async def ensure_vms_integration_indexes() -> None:
    try:
        await integrations_collection.create_index(
            "source_id", unique=True, name="idx_ccc_vms_source_id"
        )
        await integrations_collection.create_index("enabled", name="idx_ccc_vms_enabled")
    except Exception:
        pass


async def list_integrations(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if enabled_only:
        query["enabled"] = True
    cursor = integrations_collection.find(query).sort([("source_id", 1)])
    return [integration_to_public(doc) async for doc in cursor]


async def get_integration(source_id: str) -> Optional[dict]:
    sid = validate_source_id(source_id)
    doc = await integrations_collection.find_one({"source_id": sid})
    return doc


async def get_integration_public(source_id: str) -> Optional[dict[str, Any]]:
    doc = await get_integration(source_id)
    return integration_to_public(doc) if doc else None


def _merge_capabilities(raw: Any) -> dict[str, bool]:
    caps = dict(DEFAULT_CAPABILITIES)
    if isinstance(raw, dict):
        for k in caps:
            if k in raw and raw[k] is not None:
                caps[k] = bool(raw[k])
    return caps


def _merge_endpoints(raw: Any) -> dict[str, str]:
    endpoints = dict(DEFAULT_ENDPOINTS)
    if isinstance(raw, dict):
        for k in endpoints:
            if raw.get(k):
                endpoints[k] = str(raw[k]).strip()
    return endpoints


async def create_integration(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    sid = validate_source_id(str(body.get("source_id") or ""))
    existing = await integrations_collection.find_one({"source_id": sid})
    if existing:
        raise VmsIntegrationError(f"source_id already exists: {sid}")
    vendor = str(body.get("vendor_type") or "generic_rest").strip().lower()
    if vendor not in VENDOR_TYPES:
        raise VmsIntegrationError(f"vendor_type must be one of {sorted(VENDOR_TYPES)}")
    base_url = str(body.get("base_url") or "").strip().rstrip("/")
    if not base_url.startswith("https://") and not base_url.startswith("http://"):
        raise VmsIntegrationError("base_url must be http(s)://…")
    auth_type = str(body.get("auth_type") or "bearer").strip().lower()
    if auth_type not in AUTH_TYPES:
        raise VmsIntegrationError(f"auth_type must be one of {sorted(AUTH_TYPES)}")
    credential_plain = str(body.get("credential") or body.get("api_key") or "").strip()
    ingest_secret_plain = secrets.token_urlsafe(32)
    now = _utcnow()
    doc = {
        "source_id": sid,
        "label": str(body.get("label") or sid)[:120],
        "vendor_type": vendor,
        "base_url": base_url,
        "enabled": bool(body.get("enabled", False)),
        "capabilities": _merge_capabilities(body.get("capabilities")),
        "endpoints": _merge_endpoints(body.get("endpoints")),
        "auth_type": auth_type,
        "auth_header": str(body.get("auth_header") or "Authorization")[:64],
        "tls_verify": bool(body.get("tls_verify", True)),
        "timeout_seconds": max(3, min(int(body.get("timeout_seconds") or 10), 60)),
        "health": "unknown",
        "last_error": "",
        "credential_encrypted": encrypt_credential(credential_plain) if credential_plain else "",
        "ingest_secret_hash": hash_integration_secret(ingest_secret_plain),
        "created_at": now,
        "updated_at": now,
    }
    await integrations_collection.insert_one(doc)
    return integration_to_public(doc, include_secret_once=ingest_secret_plain), ingest_secret_plain


async def update_integration(source_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    doc = await get_integration(source_id)
    if not doc:
        raise VmsIntegrationError("Integration not found")
    updates: dict[str, Any] = {"updated_at": _utcnow()}
    if "label" in patch and patch["label"] is not None:
        updates["label"] = str(patch["label"])[:120]
    if "base_url" in patch and patch["base_url"] is not None:
        base_url = str(patch["base_url"]).strip().rstrip("/")
        if not base_url.startswith("http"):
            raise VmsIntegrationError("base_url must be http(s)://…")
        updates["base_url"] = base_url
    if "enabled" in patch and patch["enabled"] is not None:
        updates["enabled"] = bool(patch["enabled"])
    if "capabilities" in patch and isinstance(patch["capabilities"], dict):
        updates["capabilities"] = _merge_capabilities(
            {**(doc.get("capabilities") or {}), **patch["capabilities"]}
        )
    if "endpoints" in patch and isinstance(patch["endpoints"], dict):
        updates["endpoints"] = _merge_endpoints(
            {**(doc.get("endpoints") or {}), **patch["endpoints"]}
        )
    if "auth_type" in patch and patch["auth_type"] is not None:
        auth_type = str(patch["auth_type"]).strip().lower()
        if auth_type not in AUTH_TYPES:
            raise VmsIntegrationError(f"auth_type must be one of {sorted(AUTH_TYPES)}")
        updates["auth_type"] = auth_type
    if "auth_header" in patch and patch["auth_header"] is not None:
        updates["auth_header"] = str(patch["auth_header"])[:64]
    if "tls_verify" in patch and patch["tls_verify"] is not None:
        updates["tls_verify"] = bool(patch["tls_verify"])
    if "timeout_seconds" in patch and patch["timeout_seconds"] is not None:
        updates["timeout_seconds"] = max(3, min(int(patch["timeout_seconds"]), 60))
    if patch.get("credential") or patch.get("api_key"):
        plain = str(patch.get("credential") or patch.get("api_key") or "").strip()
        if plain:
            updates["credential_encrypted"] = encrypt_credential(plain)
    await integrations_collection.update_one({"source_id": doc["source_id"]}, {"$set": updates})
    refreshed = await get_integration(doc["source_id"])
    return integration_to_public(refreshed or doc)


async def delete_integration(source_id: str) -> bool:
    doc = await get_integration(source_id)
    if not doc:
        return False
    await integrations_collection.delete_one({"source_id": doc["source_id"]})
    return True


async def rotate_ingest_secret(source_id: str) -> tuple[dict[str, Any], str]:
    doc = await get_integration(source_id)
    if not doc:
        raise VmsIntegrationError("Integration not found")
    secret = secrets.token_urlsafe(32)
    await integrations_collection.update_one(
        {"source_id": doc["source_id"]},
        {
            "$set": {
                "ingest_secret_hash": hash_integration_secret(secret),
                "updated_at": _utcnow(),
            }
        },
    )
    refreshed = await get_integration(doc["source_id"])
    return integration_to_public(refreshed or doc, include_secret_once=secret), secret


async def verify_ingest_secret(source_id: str, provided: str) -> bool:
    doc = await get_integration(source_id)
    if not doc:
        return False
    hashed = doc.get("ingest_secret_hash") or ""
    return bool(hashed and provided and verify_integration_secret(provided, hashed))


async def touch_integration_health(
    source_id: str,
    *,
    health: str,
    error: str = "",
    success: bool = False,
) -> None:
    now = _utcnow()
    updates: dict[str, Any] = {
        "health": health,
        "last_seen": now,
        "last_error": error[:500] if error else "",
        "updated_at": now,
    }
    if success:
        updates["last_success"] = now
        updates["last_test_at"] = now
    await integrations_collection.update_one({"source_id": source_id}, {"$set": updates})


def integration_config_for_adapter(doc: dict) -> dict[str, Any]:
    """Internal config passed to GenericRestVmsSource (includes decrypted credential)."""
    from app.services.ccc_vms_credential_crypto import decrypt_credential

    cred = ""
    enc = doc.get("credential_encrypted") or ""
    if enc:
        cred = decrypt_credential(enc)
    return {
        "source_id": doc["source_id"],
        "label": doc.get("label") or doc["source_id"],
        "vendor_type": doc.get("vendor_type") or "generic_rest",
        "base_url": doc.get("base_url") or "",
        "enabled": bool(doc.get("enabled")),
        "capabilities": _merge_capabilities(doc.get("capabilities")),
        "endpoints": _merge_endpoints(doc.get("endpoints")),
        "auth_type": doc.get("auth_type") or "bearer",
        "auth_header": doc.get("auth_header") or "Authorization",
        "credential": cred,
        "tls_verify": bool(doc.get("tls_verify", True)),
        "timeout_seconds": int(doc.get("timeout_seconds") or 10),
    }
