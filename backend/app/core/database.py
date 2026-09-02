import re
import motor.motor_asyncio
from bson.objectid import ObjectId
from datetime import datetime, timezone
from typing import Optional
import bcrypt
import logging
import sys
import os

# Add the backend directory to the Python path for config import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.config import config

# --- Connection ---
MONGO_DETAILS = config.mongodb_uri
DATABASE_NAME = config.database_name
try:
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_DETAILS)
    database = client[DATABASE_NAME]
    user_collection = database.get_collection("users")
    camera_collection = database.get_collection("cameras")
    recording_sessions_collection = database.get_collection("recording_sessions")
    recording_status_logs_collection = database.get_collection("recording_status_logs")
    alarm_rules_collection = database.get_collection("alarm_rules")
    camera_sequences_collection = database.get_collection("camera_sequences")
    events_collection = database.get_collection("events")
    pilot_recording_collection = database.get_collection("pilot_recording")
    logging.info(f"✅ Successfully connected to MongoDB: {DATABASE_NAME}")
except Exception as e:
    logging.error(f"❌ Could not connect to MongoDB. Is the server running? Error: {e}")

# --- Helpers ---
from app.core.roles import stored_role_label
from app.services.camera_access import camera_access_public


def user_helper(user) -> dict:
    """Converts a user document from MongoDB into a JSON-serializable dict."""
    if not user:
        return None
    return {
        "id": str(user["_id"]),
        "name": user.get("name") or user.get("username") or "",
        "username": user.get("username") or user.get("name") or "",
        "role": stored_role_label(user) or user.get("role"),
        "lastLogin": user.get("lastLogin"),
        "status": user.get("status"),
        "email": user.get("email", ""),
        "permissions": user.get("permissions", []),
        "cameraAccess": camera_access_public(user),
    }

# --- User Database Functions ---
async def get_users():
    """Retrieve all users from the database."""
    users = []
    async for user in user_collection.find():
        users.append(user_helper(user))
    return users

async def get_user_by_name(name: str):
    """Find a single user by display name or login username (case-insensitive)."""
    key = (name or "").strip()
    if not key:
        return None
    pattern = {"$regex": f"^{re.escape(key)}$", "$options": "i"}
    return await user_collection.find_one({"$or": [{"name": pattern}, {"username": pattern}]})

async def get_user_by_id(user_id: str):
    """Find a single user by MongoDB id."""
    try:
        return await user_collection.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None

async def backfill_usernames() -> int:
    """Set username=name for legacy users (unique index on username)."""
    updated = 0
    async for user in user_collection.find({}):
        name = (user.get("name") or "").strip()
        username = (user.get("username") or "").strip()
        if not name or username == name:
            continue
        conflict = await user_collection.find_one(
            {
                "_id": {"$ne": user["_id"]},
                "$or": [{"username": name}, {"name": name}],
            }
        )
        if conflict:
            logging.warning(
                "Skipping username backfill for user %s: '%s' already used by %s",
                user["_id"],
                name,
                conflict.get("_id"),
            )
            continue
        await user_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"username": name, "name": name}},
        )
        updated += 1
    if updated:
        logging.info("Backfilled username for %s user(s)", updated)
    return updated

async def add_user(user_data: dict) -> dict:
    """Creates a new user with guaranteed fields for status and lastLogin."""
    password = user_data.get("password")
    if not password:
        raise ValueError("Password is required.")
    name = (user_data.get("name") or "").strip()
    if not name:
        raise ValueError("Username is required.")

    existing = await user_collection.find_one({"$or": [{"name": name}, {"username": name}]})
    if existing:
        raise ValueError(f"User '{name}' already exists")

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    new_user_doc = {
        "name": name,
        "username": name,
        "role": user_data.get("role"),
        "password": hashed_password,
        "email": user_data.get("email"),
        "permissions": user_data.get("permissions", []),
        "cameraAccess": user_data.get("cameraAccess") or {
            "allowedCameraGroups": [],
            "allowedCameraUids": [],
        },
        "status": "Active",
        "lastLogin": "Never"
    }
    user = await user_collection.insert_one(new_user_doc)
    new_user = await user_collection.find_one({"_id": user.inserted_id})
    return user_helper(new_user)

async def update_user(id: str, user_data: dict):
    """Update a user in the database by their ID.
    Password is hashed before writing — never stored as plaintext.
    """
    user = await user_collection.find_one({"_id": ObjectId(id)})
    if user:
        update_doc = dict(user_data)
        update_doc.pop("id", None)
        if "name" in update_doc and update_doc["name"]:
            update_doc["username"] = update_doc["name"].strip()
            update_doc["name"] = update_doc["username"]
        if "password" in update_doc and not update_doc["password"]:
            update_doc.pop("password", None)
        if 'password' in update_doc and update_doc['password']:
            hashed_password = bcrypt.hashpw(update_doc['password'].encode('utf-8'), bcrypt.gensalt())
            update_doc['password'] = hashed_password
        await user_collection.update_one({"_id": ObjectId(id)}, {"$set": update_doc})
        user = await user_collection.find_one({"_id": ObjectId(id)})
        return user_helper(user)
    return None

async def delete_user(id: str):
    """Delete a user from the database by their ID."""
    user = await user_collection.find_one({"_id": ObjectId(id)})
    if user:
        from app.services.session_service import revoke_sessions_for_user

        await revoke_sessions_for_user(id)
        await user_collection.delete_one({"_id": ObjectId(id)})
        return True
    return False

# --- Camera Database Functions ---
async def get_all_cameras_from_db():
    """Fetches all raw camera documents from the database for the discovery service."""
    cameras = []
    async for camera in camera_collection.find({}):
        camera['_id'] = str(camera['_id']) # Make ObjectId JSON serializable
        cameras.append(camera)
    return cameras


async def backfill_all_camera_rtsp_urls() -> int:
    """Ensure every camera RTSP URLs match current username/password."""
    from app.services.rtsp_utils import build_camera_rtsp_urls, rtsp_url_credentials_stale, sync_camera_rtsp_urls

    updated = 0
    async for cam in camera_collection.find({}):
        if not (cam.get("ip_address") or "").strip():
            continue
        has_all = cam.get("sub_rtsp_url") and cam.get("main_rtsp_url")
        stale = rtsp_url_credentials_stale(cam)
        if has_all and not stale:
            continue
        urls = sync_camera_rtsp_urls(cam)
        if not urls.get("sub_rtsp_url"):
            urls = build_camera_rtsp_urls(cam)
        if not urls.get("sub_rtsp_url"):
            continue
        patch = {
            k: urls[k]
            for k in (
                "main_rtsp_url",
                "sub_rtsp_url",
                "rtsp_url_source",
                "main_channel",
                "sub_channel",
                "recording_channel",
            )
            if k in urls
        }
        await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
        updated += 1
    if updated:
        logging.info(f"Backfilled RTSP URLs for {updated} camera(s)")
    return updated

def recording_session_helper(doc) -> dict:
    if not doc:
        return None
    return {
        "id": str(doc["_id"]),
        "camera_id": doc.get("camera_id"),
        "status": doc.get("status"),
        "started_at": doc.get("started_at"),
        "stopped_at": doc.get("stopped_at"),
        "storage_path": doc.get("storage_path"),
        "playlist_file": doc.get("playlist_file"),
        "segment_count": doc.get("segment_count", 0),
        "total_bytes": doc.get("total_bytes", 0),
        "storage_used_gb": doc.get("storage_used_gb", 0.0),
        "latest_segment_time": doc.get("latest_segment_time"),
        "rtsp_url_masked": doc.get("rtsp_url_masked"),
        "stream_profile": doc.get("stream_profile"),
        "segment_seconds": doc.get("segment_seconds"),
        "last_stats_at": doc.get("last_stats_at"),
        "bytes_per_hour": doc.get("bytes_per_hour"),
        "gb_per_day_estimate": doc.get("gb_per_day_estimate"),
        "ffmpeg_alive": doc.get("ffmpeg_alive"),
    }


async def insert_recording_status_log(entry: dict) -> None:
    await recording_status_logs_collection.insert_one(entry)


async def create_recording_session(
    camera_id: str,
    storage_path: str,
    rtsp_url_masked: str,
    *,
    camera_uid: str = "",
    camera_name: str = "",
    ip_address: str = "",
    stream_profile: str = "main/101 copy",
    segment_seconds: str = "2",
) -> dict:
    doc = {
        "camera_id": camera_id,
        "camera_uid": camera_uid or "",
        "camera_name": camera_name or "",
        "ip_address": ip_address or "",
        "status": "recording",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "stopped_at": None,
        "storage_path": storage_path,
        "file_path": storage_path,
        "playlist_file": "index.m3u8",
        "segment_count": 0,
        "total_bytes": 0,
        "storage_used_gb": 0.0,
        "latest_segment_time": None,
        "rtsp_url_masked": rtsp_url_masked,
        "stream_profile": stream_profile,
        "segment_seconds": segment_seconds,
    }
    result = await recording_sessions_collection.insert_one(doc)
    created = await recording_sessions_collection.find_one({"_id": result.inserted_id})
    return recording_session_helper(created)


async def update_recording_session(session_id: str, updates: dict) -> Optional[dict]:
    oid = ObjectId(session_id)
    await recording_sessions_collection.update_one({"_id": oid}, {"$set": updates})
    doc = await recording_sessions_collection.find_one({"_id": oid})
    return recording_session_helper(doc)


async def get_recording_session(session_id: str):
    doc = await recording_sessions_collection.find_one({"_id": ObjectId(session_id)})
    return recording_session_helper(doc)


async def get_active_recording_session(camera_id: str):
    doc = await recording_sessions_collection.find_one(
        {"camera_id": camera_id, "status": "recording"},
        sort=[("started_at", -1)],
    )
    return recording_session_helper(doc)


async def list_recording_sessions(camera_id: str | None = None, limit: int = 50):
    query = {"camera_id": camera_id} if camera_id else {}
    sessions = []
    cursor = recording_sessions_collection.find(query).sort("started_at", -1).limit(limit)
    async for doc in cursor:
        sessions.append(recording_session_helper(doc))
    return sessions


PILOT_DOC_ID = "phase1"


async def cleanup_legacy_pilot_recording() -> None:
    """Retire Phase-1 pilot recording — stop any active pilot FFmpeg and mark removed."""
    from datetime import datetime, timezone

    doc = await pilot_recording_collection.find_one({"_id": PILOT_DOC_ID})
    if not doc:
        return
    if doc.get("status") == "active":
        from app.services.video_recording import is_camera_recording, stop_camera_recording

        for cid in doc.get("camera_ids") or []:
            try:
                cid_str = str(cid)
                if await is_camera_recording(cid_str):
                    await stop_camera_recording(cid_str)
            except Exception as exc:
                logging.warning("[RECORDING] Pilot cleanup stop %s: %s", cid, exc)
    await pilot_recording_collection.update_one(
        {"_id": PILOT_DOC_ID},
        {
            "$set": {
                "status": "removed",
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "stopped_reason": "pilot_feature_removed",
            }
        },
        upsert=False,
    )
    logging.info("[RECORDING] Legacy pilot recording retired (no auto-start)")


async def create_camera(camera_data: dict) -> dict:
    """Insert a new camera (no upsert). Caller must check duplicates first."""
    from datetime import datetime, timezone
    from app.services.camera_form import find_duplicate_camera, duplicate_conflict_response

    dup = await find_duplicate_camera(camera_data)
    if dup:
        existing_doc, conflict = dup
        body, status = duplicate_conflict_response(existing_doc, conflict, camera_data)
        raise ValueError(body.get("message") or "Duplicate camera")

    fields = dict(camera_data)
    fields["registered_at"] = datetime.now(timezone.utc).isoformat()
    ins = await camera_collection.insert_one(fields)
    created = await camera_collection.find_one({"_id": ins.inserted_id})
    created["_id"] = str(created["_id"])
    return created


async def add_camera(camera_data: dict) -> dict:
    """Creates or updates a camera by stable IP / camera_uid (upsert)."""
    result = await upsert_camera_by_ip(camera_data)
    return result["camera"]


async def upsert_camera_by_ip(camera_data: dict) -> dict:
    """Upsert camera by ip_address / camera_uid. Preserves recording_storage_id."""
    from app.services.camera_sync import finalize_camera_document
    from app.services.camera_uid import make_camera_uid

    ip_address = (camera_data.get("ip_address") or camera_data.get("ip") or "").strip()
    if not ip_address:
        raise ValueError("IP address is required")

    camera_uid = make_camera_uid(ip_address)
    if not camera_uid:
        raise ValueError("Invalid IP address")

    existing = await camera_collection.find_one(
        {"$or": [{"camera_uid": camera_uid}, {"ip_address": ip_address}, {"ip": ip_address}]}
    )

    merged = {**(existing or {}), **camera_data, "ip_address": ip_address}
    fields = finalize_camera_document(merged, existing=existing)

    if existing:
        preserve = {}
        for key in ("recording_storage_id", "registered_at"):
            if existing.get(key) is not None:
                preserve[key] = existing[key]
        fields.update(preserve)
        from app.services.go2rtc_workers import WORKERS_ENABLED, ensure_camera_worker_assigned

        if WORKERS_ENABLED:
            fields = await ensure_camera_worker_assigned(fields, existing=existing)
        await camera_collection.update_one({"_id": existing["_id"]}, {"$set": fields})
        updated = await camera_collection.find_one({"_id": existing["_id"]})
        updated["_id"] = str(updated["_id"])
        return {"camera": updated, "created": False}

    if "registered_at" not in fields:
        fields["registered_at"] = datetime.now(timezone.utc).isoformat()

    from app.services.go2rtc_workers import WORKERS_ENABLED, ensure_camera_worker_assigned

    if WORKERS_ENABLED:
        fields = await ensure_camera_worker_assigned(fields, existing=None)

    ins = await camera_collection.insert_one(fields)
    created = await camera_collection.find_one({"_id": ins.inserted_id})
    created["_id"] = str(created["_id"])
    return {"camera": created, "created": True}


async def mark_cameras_inactive_not_in_ips(active_ips: set) -> int:
    """Mark cameras missing from an import batch as inactive (never delete)."""
    count = 0
    async for cam in camera_collection.find({}):
        ip = (cam.get("ip_address") or "").strip()
        if ip and ip not in active_ips and cam.get("is_active") is not False:
            await camera_collection.update_one(
                {"_id": cam["_id"]},
                {"$set": {"is_active": False}},
            )
            count += 1
    return count


async def update_camera(id: str, camera_data: dict):
    """Update a camera in the database by their ID."""
    import logging
    from app.services.camera_sync import finalize_camera_document
    from app.services.camera_uid import make_camera_uid
    camera = await camera_collection.find_one({"_id": ObjectId(id)})
    if camera:
        if "password" in camera_data:
            password = camera_data.get("password")
            if password is None:
                password = ""
            else:
                password = str(password).strip()
            camera_data["password"] = password if password else ""

        if "port" in camera_data:
            camera_data["port"] = int(camera_data["port"])

        merged = finalize_camera_document({**camera, **camera_data}, existing=camera)
        if "ip_address" in camera_data:
            ip = (camera_data.get("ip_address") or "").strip()
            if ip:
                merged["camera_uid"] = make_camera_uid(ip)

        log_data = {k: ('***' if k == 'password' and v else v) for k, v in merged.items()}
        logging.info(f"Updating camera document: {log_data}")

        await camera_collection.update_one({"_id": ObjectId(id)}, {"$set": merged})
        camera = await camera_collection.find_one({"_id": ObjectId(id)})
        camera['_id'] = str(camera['_id'])
        return camera
    return None

async def delete_camera(id: str):
    """Delete a camera from the database by their ID."""
    camera = await camera_collection.find_one({"_id": ObjectId(id)})
    if camera:
        await camera_collection.delete_one({"_id": ObjectId(id)})
        return True
    return False


async def _drop_legacy_unique_camera_name_index() -> None:
    """Remove unique index on cameras.name — names may repeat across sites/buildings."""
    try:
        async for idx in camera_collection.list_indexes():
            key = idx.get("key") or {}
            if list(key.keys()) == ["name"] and idx.get("unique"):
                await camera_collection.drop_index(idx["name"])
                logging.info("[DB] Dropped unique index on cameras.name: %s", idx["name"])
    except Exception as exc:
        logging.warning("[DB] Could not drop legacy unique name index: %s", exc)


async def _drop_legacy_worker_id_indexes(collection) -> None:
    """Drop old worker_id indexes (e.g. worker_id_1) before creating idx_* names."""
    try:
        async for idx in collection.list_indexes():
            key = idx.get("key") or {}
            if list(key.keys()) != ["worker_id"]:
                continue
            name = idx.get("name")
            if name and not str(name).startswith("idx_"):
                await collection.drop_index(name)
                logging.info("[DB] Dropped legacy worker_id index %s on %s", name, collection.name)
    except Exception as exc:
        logging.debug("[DB] worker_id index cleanup on %s: %s", collection.name, exc)


async def ensure_database_indexes() -> None:
    """Create indexes for camera and recording session lookups."""
    locations_collection = database.get_collection("locations")
    try:
        await _drop_legacy_unique_camera_name_index()
        await camera_collection.create_index(
            "camera_uid",
            unique=True,
            name="idx_camera_uid_unique",
            partialFilterExpression={"camera_uid": {"$type": "string", "$gt": ""}},
        )
        await camera_collection.create_index(
            "ip_address",
            unique=True,
            name="idx_ip_address_unique",
            partialFilterExpression={"ip_address": {"$type": "string", "$gt": ""}},
        )
        await camera_collection.create_index("name", name="idx_camera_name")
        await camera_collection.create_index("site", name="idx_camera_site")
        await camera_collection.create_index("building", name="idx_camera_building")
        await camera_collection.create_index("floor", name="idx_camera_floor")
        await camera_collection.create_index("camera_group", name="idx_camera_group")
        await camera_collection.create_index("is_active", name="idx_camera_is_active")
        await _drop_legacy_worker_id_indexes(camera_collection)
        await camera_collection.create_index("worker_id", name="idx_camera_worker_id")
        from app.services.go2rtc_workers import ensure_workers_indexes

        await ensure_workers_indexes()
        from app.services.session_service import ensure_session_indexes
        from app.services.audit_service import ensure_audit_indexes

        await ensure_session_indexes()
        await ensure_audit_indexes()
        from app.services.alarm_rule_service import ensure_alarm_rule_indexes
        from app.services.camera_sequence_service import ensure_camera_sequence_indexes
        from app.services.event_service import ensure_event_indexes

        await ensure_alarm_rule_indexes()
        await ensure_camera_sequence_indexes()
        await ensure_event_indexes()
        try:
            await camera_collection.drop_index("idx_camera_online")
        except Exception:
            pass
        await recording_sessions_collection.create_index("camera_uid", name="idx_session_camera_uid")
        await recording_sessions_collection.create_index("ip_address", name="idx_session_ip_address")
        await recording_sessions_collection.create_index("started_at", name="idx_session_started_at")
        await locations_collection.create_index(
            [("slug", 1), ("type", 1)],
            unique=True,
            name="idx_location_slug_type",
        )
        logging.info("[DB] Camera/recording/location indexes ensured")
    except Exception as exc:
        logging.warning("[DB] Index creation skipped or partial: %s", exc)
