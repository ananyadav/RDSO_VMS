"""Stable IP-based camera identity for recordings, playback, and go2rtc."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from app.services.camera_uid import camera_display_name, ip_from_camera_uid, make_camera_uid
from app.core.database import camera_collection, recording_sessions_collection

logger = logging.getLogger(__name__)

_IP_FROM_RTSP = re.compile(r"rtsp://([^:/]+)", re.IGNORECASE)
LEGACY_UNMAPPED_UID = "legacy_unmapped"
LEGACY_UNMAPPED_NAME = "Legacy / Unmapped Recordings"

_ip_to_folder_cache: Dict[str, str] = {}
_cache_built = False


def _recordings_dir():
    from app.services.video_recording import RECORDINGS_DIR
    return RECORDINGS_DIR


def _ip_from_rtsp(url: str) -> Optional[str]:
    if not url:
        return None
    match = _IP_FROM_RTSP.match(url.strip())
    return match.group(1) if match else None


def folder_has_recordings(folder_key: str) -> bool:
    RECORDINGS_DIR = _recordings_dir()
    cam_dir = RECORDINGS_DIR / folder_key
    if not cam_dir.is_dir():
        return False
    sessions = cam_dir / "sessions"
    if sessions.is_dir() and any(sessions.iterdir()):
        return True
    return any(cam_dir.rglob("seg_*.ts")) or any(cam_dir.rglob("*.ts"))


async def build_ip_to_folder_map() -> Dict[str, str]:
    global _ip_to_folder_cache, _cache_built
    if _cache_built:
        return _ip_to_folder_cache

    ip_to_folder: Dict[str, str] = {}
    async for doc in recording_sessions_collection.find(
        {}, {"camera_id": 1, "camera_uid": 1, "ip_address": 1, "rtsp_url_masked": 1, "storage_path": 1}
    ):
        folder = None
        path = doc.get("storage_path") or ""
        if path:
            folder = path.split("/", 1)[0]
        if not folder:
            folder = doc.get("camera_id")
        ip = (doc.get("ip_address") or "").strip() or _ip_from_rtsp(doc.get("rtsp_url_masked") or "")
        uid = doc.get("camera_uid")
        if uid and ip_from_camera_uid(uid):
            ip = ip or ip_from_camera_uid(uid) or ""
        if ip and folder:
            ip_to_folder.setdefault(ip, str(folder))

    _ip_to_folder_cache = ip_to_folder
    _cache_built = True
    return ip_to_folder


def reset_identity_cache() -> None:
    global _cache_built
    _cache_built = False


async def get_camera_by_ref(ref: str) -> Optional[dict]:
    """Resolve a camera document from MongoDB _id or camera_uid."""
    ref = (ref or "").strip()
    if not ref or ref == LEGACY_UNMAPPED_UID:
        return None

    if ref.startswith("ip_"):
        return await camera_collection.find_one({"camera_uid": ref})

    try:
        doc = await camera_collection.find_one({"_id": ObjectId(ref)})
        if doc:
            return doc
    except (InvalidId, TypeError):
        pass

    return await camera_collection.find_one({"camera_uid": ref})


async def resolve_camera_uid(ref: str) -> Optional[str]:
    if ref == LEGACY_UNMAPPED_UID:
        return LEGACY_UNMAPPED_UID
    cam = await get_camera_by_ref(ref)
    if cam:
        uid = cam.get("camera_uid")
        if uid:
            return str(uid)
        ip = (cam.get("ip_address") or "").strip()
        return make_camera_uid(ip)
    if ref.startswith("ip_"):
        return ref
    ip = ip_from_camera_uid(ref)
    if ip:
        return ref
    return None


async def storage_folder_keys_for_uid(camera_uid: str) -> List[str]:
    """All on-disk folder keys that may hold footage for this camera_uid."""
    if camera_uid == LEGACY_UNMAPPED_UID:
        return await unmapped_storage_folder_keys()

    keys: List[str] = [camera_uid]
    ip = ip_from_camera_uid(camera_uid)

    cam = await camera_collection.find_one({"camera_uid": camera_uid})
    if cam:
        mongo_id = str(cam["_id"])
        if mongo_id not in keys:
            keys.append(mongo_id)
        stored = cam.get("recording_storage_id")
        if stored and str(stored) not in keys:
            keys.append(str(stored))

    if ip:
        legacy = (await build_ip_to_folder_map()).get(ip)
        if legacy and legacy not in keys:
            keys.append(legacy)

    extra_ids: Set[str] = set()
    session_filter: dict = {"$or": [{"camera_uid": camera_uid}]}
    if ip:
        session_filter["$or"].append({"ip_address": ip})
    session_filter["$or"].append({"camera_id": {"$in": keys}})

    async for sess in recording_sessions_collection.find(session_filter, {"camera_id": 1, "storage_path": 1}):
        cid = sess.get("camera_id")
        if cid:
            extra_ids.add(str(cid))
        path = sess.get("storage_path") or ""
        if path:
            extra_ids.add(path.split("/", 1)[0])

    for k in extra_ids:
        if k not in keys:
            keys.append(k)

    return list(dict.fromkeys(keys))


async def recording_session_mongo_filter(camera_ref: str) -> dict:
    """MongoDB filter — primary camera_uid, then ip_address, then legacy camera_id."""
    if camera_ref == LEGACY_UNMAPPED_UID:
        return await unmapped_session_filter()

    camera_uid = await resolve_camera_uid(camera_ref)
    clauses: List[dict] = []

    if camera_uid:
        clauses.append({"camera_uid": camera_uid})
        ip = ip_from_camera_uid(camera_uid)
        if ip:
            clauses.append({"ip_address": ip})

    legacy_ids = await storage_folder_keys_for_uid(camera_uid or camera_ref)
    mongo_ids = [x for x in legacy_ids if x != camera_uid and not x.startswith("ip_")]
    if mongo_ids:
        clauses.append({"camera_id": {"$in": mongo_ids}})

    if not clauses:
        return {"camera_id": camera_ref}

    seen: set[str] = set()
    unique: List[dict] = []
    for clause in clauses:
        key = str(sorted(clause.items()))
        if key not in seen:
            seen.add(key)
            unique.append(clause)

    return unique[0] if len(unique) == 1 else {"$or": unique}


async def unmapped_session_filter() -> dict:
    """Sessions with no resolvable camera_uid on any current camera."""
    mapped_uids: Set[str] = set()
    mapped_ips: Set[str] = set()
    async for cam in camera_collection.find({}, {"camera_uid": 1, "ip_address": 1}):
        uid = cam.get("camera_uid")
        if uid:
            mapped_uids.add(str(uid))
        ip = (cam.get("ip_address") or "").strip()
        if ip:
            mapped_ips.add(ip)

    return {
        "$and": [
            {
                "$or": [
                    {"camera_uid": {"$exists": False}},
                    {"camera_uid": ""},
                    {"camera_uid": None},
                ]
            },
            {
                "$or": [
                    {"ip_address": {"$exists": False}},
                    {"ip_address": ""},
                    {"ip_address": None},
                    {"ip_address": {"$nin": list(mapped_ips)}},
                ]
            },
        ]
    }


async def unmapped_storage_folder_keys() -> List[str]:
    """Disk folders for sessions that could not be mapped to a camera_uid."""
    mapped = set(await storage_folder_keys_for_all_cameras())
    keys: List[str] = []
    RECORDINGS_DIR = _recordings_dir()
    if not RECORDINGS_DIR.is_dir():
        return keys
    filt = await unmapped_session_filter()
    session_folders: Set[str] = set()
    async for sess in recording_sessions_collection.find(filt, {"camera_id": 1, "storage_path": 1}):
        path = sess.get("storage_path") or ""
        if path:
            session_folders.add(path.split("/", 1)[0])
        elif sess.get("camera_id"):
            session_folders.add(str(sess["camera_id"]))

    for child in RECORDINGS_DIR.iterdir():
        if child.is_dir() and child.name not in mapped:
            keys.append(child.name)
    for folder in session_folders:
        if folder not in keys:
            keys.append(folder)
    return list(dict.fromkeys(keys))


async def storage_folder_keys_for_all_cameras() -> List[str]:
    keys: List[str] = []
    async for cam in camera_collection.find({}, {"camera_uid": 1, "_id": 1, "recording_storage_id": 1}):
        uid = cam.get("camera_uid")
        if uid:
            keys.extend(await storage_folder_keys_for_uid(str(uid)))
    return list(dict.fromkeys(keys))


async def backfill_camera_uids() -> int:
    updated = 0
    async for cam in camera_collection.find({}):
        ip = (cam.get("ip_address") or "").strip()
        uid = make_camera_uid(ip)
        if not uid:
            continue
        current = cam.get("camera_uid")
        display = camera_display_name({**cam, "display_name": cam.get("display_name")})
        patch: Dict[str, Any] = {}
        if current != uid:
            patch["camera_uid"] = uid
        if not cam.get("display_name") and display:
            patch["display_name"] = display
        if patch:
            await camera_collection.update_one({"_id": cam["_id"]}, {"$set": patch})
            updated += 1
    return updated


async def backfill_recording_sessions_identity() -> int:
    """Backfill camera_uid, ip_address, camera_name on recording sessions."""
    reset_identity_cache()
    ip_map = await build_ip_to_folder_map()
    folder_to_ip = {folder: ip for ip, folder in ip_map.items()}
    updated = 0

    async for sess in recording_sessions_collection.find({}):
        patch: Dict[str, Any] = {}
        ip = (sess.get("ip_address") or "").strip()
        if not ip:
            ip = folder_to_ip.get(sess.get("camera_id") or "") or _ip_from_rtsp(
                sess.get("rtsp_url_masked") or ""
            )
            if ip:
                patch["ip_address"] = ip

        uid = sess.get("camera_uid")
        if not uid and ip:
            patch["camera_uid"] = make_camera_uid(ip)

        if not sess.get("camera_name"):
            name = None
            cid = sess.get("camera_id")
            if cid:
                try:
                    cam = await camera_collection.find_one({"_id": ObjectId(cid)})
                except (InvalidId, TypeError):
                    cam = None
                if not cam and ip:
                    cam = await camera_collection.find_one({"ip_address": ip})
                if not cam and patch.get("camera_uid"):
                    cam = await camera_collection.find_one({"camera_uid": patch["camera_uid"]})
                if cam:
                    name = cam.get("name")
            if name:
                patch["camera_name"] = name

        if patch:
            await recording_sessions_collection.update_one({"_id": sess["_id"]}, {"$set": patch})
            updated += 1

    return updated


async def backfill_recording_storage_ids() -> int:
    """Link cameras to legacy on-disk folders by IP (backward compatibility)."""
    reset_identity_cache()
    await build_ip_to_folder_map()
    updated = 0

    async for cam in camera_collection.find({}):
        camera_id = str(cam["_id"])
        if cam.get("recording_storage_id"):
            continue
        uid = cam.get("camera_uid") or make_camera_uid(cam.get("ip_address") or "")
        storage_id = uid or camera_id
        if uid and folder_has_recordings(uid):
            storage_id = uid
        elif not folder_has_recordings(camera_id):
            ip = (cam.get("ip_address") or "").strip()
            legacy = (await build_ip_to_folder_map()).get(ip) if ip else None
            if legacy and legacy != camera_id:
                storage_id = legacy

        if storage_id != camera_id:
            await camera_collection.update_one(
                {"_id": cam["_id"]},
                {"$set": {"recording_storage_id": storage_id}},
            )
            updated += 1

    return updated


async def has_unmapped_recordings() -> bool:
    filt = await unmapped_session_filter()
    n = await recording_sessions_collection.count_documents(filt, limit=1)
    if n:
        return True
    for folder in await unmapped_storage_folder_keys():
        if folder_has_recordings(folder):
            return True
    return False


def legacy_playback_camera_item() -> dict:
    return {
        "id": LEGACY_UNMAPPED_UID,
        "cameraUid": LEGACY_UNMAPPED_UID,
        "name": LEGACY_UNMAPPED_NAME,
        "displayName": LEGACY_UNMAPPED_NAME,
        "online": False,
        "isLegacy": True,
    }
