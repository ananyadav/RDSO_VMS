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
    pilot_recording_collection = database.get_collection("pilot_recording")
    logging.info(f"✅ Successfully connected to MongoDB: {DATABASE_NAME}")
except Exception as e:
    logging.error(f"❌ Could not connect to MongoDB. Is the server running? Error: {e}")

# --- Helpers ---
def user_helper(user) -> dict:
    """Converts a user document from MongoDB into a JSON-serializable dict."""
    if not user: return None
    return {
        "id": str(user["_id"]),
        "name": user.get("name"),
        "role": user.get("role"),
        "lastLogin": user.get("lastLogin"),
        "status": user.get("status"),
        "email": user.get("email", ""),
        "permissions": user.get("permissions", []),
        "cameraAccess": user.get("cameraAccess") or {
            "allowedCameraGroups": [],
            "allowedCameraUids": [],
        },
    }

# --- User Database Functions ---
async def get_users():
    """Retrieve all users from the database."""
    users = []
    async for user in user_collection.find():
        users.append(user_helper(user))
    return users

async def get_user_by_name(name: str):
    """Find a single user by their name."""
    user = await user_collection.find_one({"name": name})
    return user

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
        username = user.get("username")
        if name and (not username or username != name):
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
    """Ensure every camera with an IP has main/sub RTSP URLs stored."""
    from app.services.rtsp_utils import build_camera_rtsp_urls

    updated = 0
    async for cam in camera_collection.find({}):
        if not (cam.get("ip_address") or "").strip():
            continue
        has_all = (
            cam.get("sub_rtsp_url")
            and cam.get("main_rtsp_url")
            and cam.get("preview_rtsp_url")
        )
        if has_all:
            continue
        urls = build_camera_rtsp_urls(cam)
        if not urls.get("sub_rtsp_url"):
            continue
        await camera_collection.update_one({"_id": cam["_id"]}, {"$set": urls})
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


def pilot_recording_helper(doc) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("_id"),
        "camera_ids": doc.get("camera_ids", []),
        "camera_names": doc.get("camera_names", []),
        "status": doc.get("status"),
        "hours": doc.get("hours"),
        "started_at": doc.get("started_at"),
        "ends_at": doc.get("ends_at"),
        "stream_profile": doc.get("stream_profile"),
        "stopped_at": doc.get("stopped_at"),
    }


async def get_pilot_recording() -> Optional[dict]:
    doc = await pilot_recording_collection.find_one({"_id": PILOT_DOC_ID})
    return pilot_recording_helper(doc)


async def save_pilot_recording(data: dict) -> dict:
    data = {**data, "_id": PILOT_DOC_ID}
    await pilot_recording_collection.replace_one({"_id": PILOT_DOC_ID}, data, upsert=True)
    return pilot_recording_helper(data)


async def list_cameras_for_pilot(limit: int = 2) -> list:
    """Pilot cameras from PILOT_CAMERA_NAMES (default Cam10,Cam8), else first N by name."""
    names_env = os.getenv("PILOT_CAMERA_NAMES", "Cam10,Cam8").strip()
    if names_env:
        wanted = [n.strip() for n in names_env.split(",") if n.strip()]
        picked = []
        for name in wanted[:limit]:
            doc = await camera_collection.find_one({"name": name})
            if doc:
                picked.append({
                    "id": str(doc["_id"]),
                    "name": doc.get("name", name),
                    "online": True,
                })
            else:
                logging.warning(f"[PILOT] Camera '{name}' not found in DB")
        if picked:
            return picked

    cameras = []
    async for doc in camera_collection.find({}).sort("name", 1):
        cameras.append({
            "id": str(doc["_id"]),
            "name": doc.get("name", "Camera"),
            "online": True,
        })
    return cameras[:limit]


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
    import logging
    from app.services.rtsp_utils import build_camera_rtsp_urls
    from app.services.camera_uid import make_camera_uid, camera_display_name

    ip_address = (camera_data.get("ip_address") or camera_data.get("ip") or "").strip()
    if not ip_address:
        raise ValueError("IP address is required")

    camera_uid = make_camera_uid(ip_address)
    if not camera_uid:
        raise ValueError("Invalid IP address")

    password = camera_data.get("password")
    if password is None:
        password = ""
    else:
        password = str(password).strip()

    existing = await camera_collection.find_one(
        {"$or": [{"camera_uid": camera_uid}, {"ip_address": ip_address}, {"ip": ip_address}]}
    )

    fields = {
        "name": camera_data.get("name") or (existing or {}).get("name") or ip_address,
        "ip_address": ip_address,
        "camera_uid": camera_uid,
        "model": camera_data.get("model", (existing or {}).get("model", "")),
        "port": int(camera_data.get("port") or (existing or {}).get("port") or 554),
        "username": camera_data.get("username", (existing or {}).get("username", "admin")),
        "type": camera_data.get("type", (existing or {}).get("type", "rtsp")),
        "recording_channel": str(
            camera_data.get("recording_channel")
            or (existing or {}).get("recording_channel")
            or "102"
        ),
        "preview_channel": str(
            camera_data.get("preview_channel")
            or (existing or {}).get("preview_channel")
            or "103"
        ),
        "ptz": bool(camera_data.get("ptz", (existing or {}).get("ptz", False))),
        "site": camera_data.get("site", (existing or {}).get("site", "")),
        "building": camera_data.get("building", (existing or {}).get("building", "")),
        "floor_group": camera_data.get("floor_group", (existing or {}).get("floor_group", "")),
        "floor": camera_data.get("floor", (existing or {}).get("floor", "")),
        "camera_group": camera_data.get("camera_group", (existing or {}).get("camera_group", "")),
        "location_path": camera_data.get("location_path", (existing or {}).get("location_path", "")),
        "is_active": camera_data.get("is_active", True) is not False,
        "online": (existing or {}).get("online", False),
        "activity": (existing or {}).get("activity", False),
    }

    if "password" in camera_data:
        fields["password"] = password

    fields["display_name"] = camera_data.get("display_name") or camera_display_name(fields)
    fields.update(build_camera_rtsp_urls({**(existing or {}), **fields}))

    if existing:
        preserve = {}
        for key in ("recording_storage_id", "registered_at"):
            if existing.get(key) is not None:
                preserve[key] = existing[key]
        fields.update(preserve)
        await camera_collection.update_one({"_id": existing["_id"]}, {"$set": fields})
        updated = await camera_collection.find_one({"_id": existing["_id"]})
        updated["_id"] = str(updated["_id"])
        return {"camera": updated, "created": False}

    fields["password"] = fields.get("password", password)
    fields["registered_at"] = datetime.now(timezone.utc).isoformat()
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
    from app.services.rtsp_utils import build_camera_rtsp_urls
    from app.services.camera_uid import make_camera_uid, camera_display_name
    camera = await camera_collection.find_one({"_id": ObjectId(id)})
    if camera:
        # Handle password - ensure it's always a string, never None
        if "password" in camera_data:
            password = camera_data.get("password")
            logging.info(f"update_camera received password: type={type(password)}, value={'***' if password else '(empty/None)'}")
            
            if password is None:
                password = ""
                logging.warning("Password was None in update, converting to empty string")
            else:
                password = str(password).strip()
            
            # Always explicitly set password field
            camera_data["password"] = password if password else ""
            
            # Double-check password is not None
            if camera_data["password"] is None:
                logging.error("CRITICAL: Password is None after processing in update! Setting to empty string.")
                camera_data["password"] = ""
        
        # Log what we're about to update (hide password)
        if "port" in camera_data:
            camera_data["port"] = int(camera_data["port"])

        merged = {**camera, **camera_data}
        if "ip_address" in camera_data:
            ip = (camera_data.get("ip_address") or "").strip()
            if ip:
                camera_data["camera_uid"] = make_camera_uid(ip)
        camera_data.update(build_camera_rtsp_urls(merged))
        merged_for_display = {**camera, **camera_data}
        if "display_name" not in camera_data:
            camera_data["display_name"] = camera_display_name(merged_for_display)

        log_data = {k: ('***' if k == 'password' and v else v) for k, v in camera_data.items()}
        logging.info(f"Updating camera document: {log_data}")

        await camera_collection.update_one({"_id": ObjectId(id)}, {"$set": camera_data})
        camera = await camera_collection.find_one({"_id": ObjectId(id)})
        
        # Verify password was saved
        saved_password = camera.get('password')
        logging.info(f"Camera updated. Password in DB: type={type(saved_password)}, value={'***' if saved_password else '(empty/None)'}")
        
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


async def ensure_database_indexes() -> None:
    """Create indexes for camera and recording session lookups."""
    locations_collection = database.get_collection("locations")
    try:
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
        await camera_collection.create_index("building", name="idx_camera_building")
        await camera_collection.create_index("floor", name="idx_camera_floor")
        await camera_collection.create_index("camera_group", name="idx_camera_group")
        await camera_collection.create_index("is_active", name="idx_camera_is_active")
        await camera_collection.create_index("online", name="idx_camera_online")
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
