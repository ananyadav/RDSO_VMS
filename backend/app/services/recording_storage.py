"""Backward-compatible re-exports — use camera_identity for new code."""

from app.services.camera_identity import (  # noqa: F401
    backfill_camera_uids,
    backfill_recording_sessions_identity,
    backfill_recording_storage_ids,
    build_ip_to_folder_map,
    folder_has_recordings,
    recording_session_mongo_filter,
    reset_identity_cache,
    resolve_camera_uid,
    storage_folder_keys_for_uid,
)


async def backfill_recording_session_camera_names() -> int:
    return await backfill_recording_sessions_identity()


async def mapped_legacy_folder_ids() -> set:
    from app.core.database import camera_collection

    mapped: set = set()
    async for cam in camera_collection.find(
        {"recording_storage_id": {"$exists": True, "$ne": None}},
        {"recording_storage_id": 1},
    ):
        stored = cam.get("recording_storage_id")
        if stored:
            mapped.add(str(stored))
    return mapped


# Legacy aliases
build_legacy_ip_map = build_ip_to_folder_map
reset_legacy_map_cache = reset_identity_cache
resolve_storage_camera_id = resolve_camera_uid
recording_camera_ids_for_query = storage_folder_keys_for_uid
