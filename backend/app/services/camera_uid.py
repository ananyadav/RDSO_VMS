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


def _name_is_ip_placeholder(value: str, ip: str) -> bool:
    """True when name is empty or still the auto-default IP label."""
    val = (value or "").strip()
    ref = (ip or "").strip()
    return not val or (ref and val == ref)


def apply_default_camera_names(doc: dict, *, existing: dict | None = None) -> dict:
    """Default name/display_name to IP; keep user-customized labels."""
    out = dict(doc)
    ip = (out.get("ip_address") or "").strip()
    if not ip:
        return out

    old_ip = ((existing or {}).get("ip_address") or "").strip()
    name = (out.get("name") or "").strip()
    display = (out.get("display_name") or "").strip()

    if not name or (old_ip and name == old_ip and ip != old_ip):
        name = ip
    if not display or (old_ip and display == old_ip and ip != old_ip):
        display = name if name != ip else ip
    elif name != ip and _name_is_ip_placeholder(display, ip) and _name_is_ip_placeholder(display, old_ip):
        display = name

    out["name"] = name
    out["display_name"] = display
    return out


def camera_display_name(cam: dict) -> str:
    display = (cam.get("display_name") or "").strip()
    if display:
        return display
    name = (cam.get("name") or "").strip()
    floor = (cam.get("floor_group") or cam.get("floor") or "").strip()
    if floor and name:
        return f"{floor} - {name}"
    return name or (cam.get("camera_uid") or str(cam.get("_id", "")))
