"""Per-user camera access filtering."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.roles import is_ops_admin
from bson.objectid import ObjectId


def _all_access() -> Dict[str, Any]:
    return {
        "all": True,
        "allowedCameraGroups": [],
        "allowedCameraUids": [],
        "allowedCameraIds": [],
    }


def normalize_camera_access(user: Optional[dict]) -> Dict[str, Any]:
    """Return effective cameraAccess for a user document."""
    if not user:
        return _all_access()

    role = (user.get("role") or "").strip().lower()
    if is_ops_admin(user):
        return _all_access()

    raw = user.get("cameraAccess") or {}

    # Legacy: explicit all-access type with no restrictions configured.
    legacy_type = (raw.get("accessType") or "").strip().lower()
    legacy_groups = [str(g).strip() for g in (raw.get("allowedGroups") or []) if str(g).strip()]
    legacy_ids = [str(cid) for cid in (raw.get("allowedCameraIds") or []) if str(cid).strip()]
    groups = [str(g).strip() for g in (raw.get("allowedCameraGroups") or legacy_groups) if str(g).strip()]
    uids = [str(u).strip() for u in (raw.get("allowedCameraUids") or []) if str(u).strip()]

    if legacy_type == "all" and not groups and not uids and not legacy_ids:
        if is_ops_admin(user):
            return _all_access()
        return {
            "all": False,
            "allowedCameraGroups": [],
            "allowedCameraUids": [],
            "allowedCameraIds": [],
        }

    return {
        "all": False,
        "allowedCameraGroups": groups,
        "allowedCameraUids": uids,
        "allowedCameraIds": legacy_ids,
    }


def is_admin(user: Optional[dict]) -> bool:
    """True for ADMIN and SUPER_ADMIN (operational CCTV privileges)."""
    return is_ops_admin(user)


def has_unrestricted_camera_access(user: Optional[dict]) -> bool:
    access = normalize_camera_access(user)
    return bool(access.get("all"))


def active_camera_filter(include_inactive: bool) -> Dict[str, Any]:
    """Mongo filter: active cameras only unless include_inactive."""
    if include_inactive:
        return {}
    return {
        "$or": [
            {"is_active": True},
            {"is_active": {"$exists": False}},
        ]
    }


def expand_allowed_groups(groups: List[str]) -> List[str]:
    """Include site-prefixed and legacy alias keys for stored group names."""
    from app.services.camera_locations import DEFAULT_SITE_NAME, legacy_camera_group_aliases, slugify

    expanded: set[str] = set()
    site_slug = slugify(DEFAULT_SITE_NAME)
    for raw in groups:
        g = (raw or "").strip()
        if not g:
            continue
        expanded.add(g)
        for alias in legacy_camera_group_aliases(g, site=DEFAULT_SITE_NAME):
            expanded.add(alias)
        if site_slug and not g.startswith(f"{site_slug}_"):
            expanded.add(f"{site_slug}_{g}")
    return list(expanded)


def build_access_filter(user: Optional[dict]) -> Dict[str, Any]:
    """MongoDB filter fragment for camera access."""
    access = normalize_camera_access(user)
    if access.get("all"):
        return {}

    clauses: List[Dict[str, Any]] = []
    groups = expand_allowed_groups(access["allowedCameraGroups"])
    uids = access["allowedCameraUids"]
    legacy_ids = access.get("allowedCameraIds") or []

    if groups:
        clauses.append({"camera_group": {"$in": groups}})
    if uids:
        clauses.append({"camera_uid": {"$in": uids}})
    if legacy_ids:
        oids = []
        for cid in legacy_ids:
            try:
                oids.append(ObjectId(cid))
            except Exception:
                continue
        if oids:
            clauses.append({"_id": {"$in": oids}})

    if not clauses:
        return {"_id": {"$exists": False}}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


def merge_query(*parts: Dict[str, Any]) -> Dict[str, Any]:
    clauses = [p for p in parts if p]
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def user_can_access_camera(
    user: Optional[dict],
    camera_id: str,
    camera_doc: Optional[dict] = None,
) -> bool:
    if user is None:
        return False
    if is_admin(user):
        return True

    access = normalize_camera_access(user)
    if access.get("all"):
        return True

    groups = access["allowedCameraGroups"]
    uids = access["allowedCameraUids"]
    legacy_ids = access.get("allowedCameraIds") or []
    if not groups and not uids and not legacy_ids:
        return False

    if camera_doc:
        cg = (camera_doc.get("camera_group") or "").strip()
        cuid = (camera_doc.get("camera_uid") or "").strip()
        doc_id = str(camera_doc.get("_id", ""))
        allowed_groups = set(expand_allowed_groups(groups))
        if allowed_groups and cg in allowed_groups:
            return True
        if uids and cuid in uids:
            return True
        if legacy_ids and doc_id in legacy_ids:
            return True
        return False

    ref = (camera_id or "").strip()
    if ref in uids:
        return True
    if ref in legacy_ids:
        return True
    return False


def user_can_access_stream(
    user: Optional[dict],
    stream: str,
    camera_doc: Optional[dict] = None,
) -> bool:
    """Check access for go2rtc stream name like {camera_uid}_sub."""
    if user is None:
        return False
    if is_admin(user):
        return True

    stream_ref = parse_stream_camera_id(stream)
    if not stream_ref:
        return False
    if camera_doc is not None:
        return user_can_access_camera(user, stream_ref, camera_doc)

    access = normalize_camera_access(user)
    if access.get("all"):
        return True

    if stream_ref in access["allowedCameraUids"]:
        return True
    if stream_ref in (access.get("allowedCameraIds") or []):
        return True
    return False


def parse_stream_camera_id(stream: str) -> Optional[str]:
    s = (stream or "").strip()
    if s.endswith("_sub"):
        return s[: -len("_sub")]
    if s.endswith("_main"):
        return s[: -len("_main")]
    return None


def camera_access_public(user: Optional[dict]) -> Dict[str, Any]:
    """Safe cameraAccess for API responses."""
    access = normalize_camera_access(user)
    return {
        "all": bool(access.get("all")),
        "allowedCameraGroups": access["allowedCameraGroups"],
        "allowedCameraUids": access["allowedCameraUids"],
    }
