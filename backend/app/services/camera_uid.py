"""Pure helpers for stable IP-based camera_uid (no DB / filesystem deps)."""

from __future__ import annotations

from typing import Optional


def make_camera_uid(ip_address: str) -> Optional[str]:
    ip = (ip_address or "").strip()
    if not ip:
        return None
    return "ip_" + ip.replace(".", "_")


def ip_from_camera_uid(camera_uid: str) -> Optional[str]:
    uid = (camera_uid or "").strip()
    if not uid.startswith("ip_"):
        return None
    parts = uid[3:].split("_")
    if len(parts) != 4:
        return None
    return ".".join(parts)


def camera_display_name(cam: dict) -> str:
    display = (cam.get("display_name") or "").strip()
    if display:
        return display
    name = (cam.get("name") or "").strip()
    floor = (cam.get("floor_group") or cam.get("floor") or "").strip()
    if floor and name:
        return f"{floor} - {name}"
    return name or (cam.get("camera_uid") or str(cam.get("_id", "")))
