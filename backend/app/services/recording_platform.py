"""RDSO 18.3.4 / 18.3.7 — open-architecture + network-accessible recording platform evidence."""

from __future__ import annotations

import os
import platform
import sys
from typing import Any


def get_open_architecture_evidence() -> dict[str, Any]:
    """18.3.4 — software runs on standard OS/hardware; no proprietary NVR appliance required."""
    return {
        "rdso_18_3_4": True,
        "open_architecture": True,
        "proprietary_hardware_required": False,
        "message": (
            "Recording software runs on standard Windows/Linux x86 hosts. "
            "It does not require a proprietary NVR/server appliance."
        ),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "supported_os": ["Windows", "Linux"],
        },
        "dependencies": [
            {
                "name": "FFmpeg",
                "role": "RTSP ingest / HLS segment recording (open binary)",
                "proprietary": False,
            },
            {
                "name": "MongoDB",
                "role": "metadata, sessions, configuration",
                "proprietary": False,
            },
            {
                "name": "OS filesystem",
                "role": "recording storage (local or OS-mounted DAS/NAS/SAN)",
                "proprietary": False,
            },
            {
                "name": "RTSP / ONVIF",
                "role": "camera stream and Profile S/G interoperability",
                "proprietary": False,
            },
        ],
        "excluded": [
            "No proprietary NVR hardware SKU",
            "No vendor-only recording appliance lock-in",
            "No in-process vendor NAS/SAN protocol stack required",
        ],
    }


def get_network_access_evidence() -> dict[str, Any]:
    """18.3.7 — management/status via authenticated network VMS API (not localhost-only logic)."""
    api_host = (os.getenv("API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    return {
        "rdso_18_3_7": True,
        "network_accessible": True,
        "message": (
            "Recording management and status are exposed through the authenticated VMS HTTP API. "
            "Clients reach the system from any network location that can access the VMS "
            "(typically via Nginx reverse proxy). Application routes do not restrict callers to localhost."
        ),
        "api_bind_default": api_host,
        "api_bind_note": (
            "Default API_HOST=127.0.0.1 is a deployment hardening choice when Nginx fronts the API "
            "on the LAN/WAN. Set API_HOST=0.0.0.0 only when intentionally exposing the API process directly."
        ),
        "access_path": [
            "Client → Nginx (LAN/WAN) → Backend /api/*",
            "Authenticated session / RBAC / camera ACL enforced on recording and storage routes",
        ],
        "localhost_only_application_logic": False,
        "auth_required": True,
        "rbac_enforced": True,
        "camera_acl_enforced": True,
    }


def get_recording_platform_status() -> dict[str, Any]:
    """Combined platform status for GET /api/recordings/capability."""
    return {
        "open_architecture": get_open_architecture_evidence(),
        "network_access": get_network_access_evidence(),
    }
