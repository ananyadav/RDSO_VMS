"""Shared third-party ingest idempotency (RDSO 18.6.22.8).

Dedupes by (scope, key) so replaying the same external_event_id / Idempotency-Key
returns the prior event without creating a second alarm/event engine path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import database

idempotency_collection = database.get_collection("ccc_integration_idempotency")

IDEMPOTENCY_KEY_MAX_LEN = 128


class IdempotencyError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def extract_idempotency_key(body: dict[str, Any], *, header_key: str = "") -> str:
    raw = (
        header_key
        or body.get("idempotency_key")
        or body.get("external_event_id")
        or body.get("event_id")
        or ""
    )
    key = str(raw).strip()[:IDEMPOTENCY_KEY_MAX_LEN]
    return key


async def ensure_idempotency_indexes() -> None:
    try:
        await idempotency_collection.create_index(
            [("scope", 1), ("key", 1)],
            unique=True,
            name="idx_ccc_ingest_idempotency",
        )
        await idempotency_collection.create_index(
            "created_at", name="idx_ccc_ingest_idempotency_created"
        )
    except Exception:
        pass


async def lookup_idempotent_result(scope: str, key: str) -> Optional[dict[str, Any]]:
    if not key:
        return None
    doc = await idempotency_collection.find_one({"scope": scope, "key": key})
    if not doc:
        return None
    result = doc.get("result")
    return result if isinstance(result, dict) else None


async def store_idempotent_result(scope: str, key: str, result: dict[str, Any]) -> None:
    if not key:
        return
    now = _utcnow()
    # Keep a compact, secret-free snapshot.
    slim = {
        k: result[k]
        for k in (
            "ok",
            "accepted",
            "event_created",
            "event_id",
            "pipeline",
            "second_alarm_engine",
            "via_existing_pipeline",
            "source_id",
            "device_id",
            "duplicate",
        )
        if k in result
    }
    if "event" in result and isinstance(result["event"], dict):
        slim["event_id"] = result["event"].get("id") or slim.get("event_id")
    try:
        await idempotency_collection.update_one(
            {"scope": scope, "key": key},
            {
                "$setOnInsert": {
                    "scope": scope,
                    "key": key,
                    "created_at": now,
                    "result": slim,
                }
            },
            upsert=True,
        )
    except Exception:
        # Race on unique index — treat as already stored.
        pass
