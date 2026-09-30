"""Application / site timezone for calendar-day queries (playback search/dates).

No prior project-wide timezone setting existed. Playback interprets YYYY-MM-DD
as a calendar day in this zone and converts bounds to UTC for storage queries.

Configure via env:

  APP_TIMEZONE=Asia/Kolkata   (default — India RDSO deployments)
  APP_TIMEZONE=UTC            (UTC calendar days)

Uses the standard-library zoneinfo (DST-aware). Invalid names fall back to UTC
with a warning. The effective (validated) IANA name is what APIs expose to clients.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_APP_TIMEZONE = "Asia/Kolkata"
_ENV_KEY = "APP_TIMEZONE"


def get_app_timezone_name() -> str:
    """Raw configured name from env (or default). Prefer get_effective_app_timezone_name()."""
    raw = (os.getenv(_ENV_KEY) or DEFAULT_APP_TIMEZONE).strip()
    return raw or DEFAULT_APP_TIMEZONE


def get_effective_app_timezone_name() -> str:
    """Validated IANA timezone name used by the app (safe for clients)."""
    name = get_app_timezone_name()
    try:
        ZoneInfo(name)
        return name
    except ZoneInfoNotFoundError:
        logger.warning(
            "[TZ] Unknown APP_TIMEZONE=%r — falling back to UTC",
            name,
        )
        return "UTC"


@lru_cache(maxsize=8)
def _zoneinfo_for(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def get_app_timezone() -> ZoneInfo | timezone:
    """Resolved tzinfo for the effective application/site timezone."""
    return _zoneinfo_for(get_effective_app_timezone_name())


def clear_app_timezone_cache() -> None:
    """Test helper — clear cached ZoneInfo after env changes."""
    _zoneinfo_for.cache_clear()


def local_day_bounds_utc(date_str: str) -> tuple[datetime, datetime]:
    """
    Half-open UTC interval [start, end) for a local calendar date YYYY-MM-DD.

    Example Asia/Kolkata 2026-09-04:
      start ≈ 2026-09-03 18:30:00+00:00
      end   ≈ 2026-09-04 18:30:00+00:00
    """
    naive = datetime.strptime(date_str, "%Y-%m-%d")
    tz = get_app_timezone()
    local_start = naive.replace(tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def get_system_time_status() -> dict:
    """VMS/OS time + network time-sync status for RDSO 18.3.6.

    Recording session timestamps and HLS program_date_time use the host OS clock.
    NTP client/server functions are provided by the OS (chrony / timesyncd / w32time),
    not by an in-process Python NTP implementation.
    """
    from app.services.os_time_sync import probe_os_time_sync

    now_utc = datetime.now(timezone.utc)
    tz_name = get_effective_app_timezone_name()
    tz = get_app_timezone()
    now_local = now_utc.astimezone(tz)
    os_sync = probe_os_time_sync()
    site = os_sync.get("site_time_source") or {}
    sync_state = os_sync.get("sync_state") or "service_unavailable"
    return {
        "utc_now": now_utc.isoformat(),
        "app_timezone": tz_name,
        "local_now": now_local.isoformat(),
        "recording_timestamps": "utc_iso_from_os_clock",
        "hls_program_date_time": "ffmpeg_uses_os_clock",
        "sync_state": sync_state,
        "synchronized": bool(os_sync.get("synchronized")),
        "ntp": {
            "in_app_ntp_server": False,
            "sync_responsibility": "operating_system",
            "service": os_sync.get("service"),
            "service_running": bool(os_sync.get("service_running")),
            "sync_state": sync_state,
            "synchronized": bool(os_sync.get("synchronized")),
            "source": os_sync.get("source"),
            "ntp_servers": list(os_sync.get("ntp_servers") or []),
            "stratum": os_sync.get("stratum"),
            "last_sync": os_sync.get("last_sync"),
            "site_time_source": {
                "capable": bool(site.get("capable")),
                "active": bool(site.get("active")),
                "mode": site.get("mode"),
                "guidance": site.get("guidance")
                or os_sync.get("guidance")
                or (
                    "Use the host OS time service so cameras and recording share a common clock."
                ),
            },
            "guidance": os_sync.get("guidance")
            or site.get("guidance")
            or (
                "Keep the VMS host synchronized via OS NTP "
                "(Windows Time / chrony / systemd-timesyncd). "
                "APP_TIMEZONE only interprets calendar days for Playback."
            ),
            "admin_actions": [
                {
                    "action": "resync",
                    "method": "POST /api/system/time/actions",
                    "requires_confirm": True,
                    "description": "Force an OS time resync (chronyc makestep / w32tm /resync).",
                },
                {
                    "action": "enable_site_ntp_server",
                    "method": "POST /api/system/time/actions",
                    "requires_confirm": True,
                    "description": (
                        "Explicitly configure the OS to act as a site NTP source where supported "
                        "(Windows w32time). Linux chrony requires host-admin conf edits."
                    ),
                },
            ],
            "details": os_sync.get("details"),
            "error": os_sync.get("error"),
        },
    }


def local_month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """Half-open UTC interval covering the local calendar month."""
    if month < 1 or month > 12:
        raise ValueError("month must be 1-12")
    start_str = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end_str = f"{year + 1:04d}-01-01"
    else:
        end_str = f"{year:04d}-{month + 1:02d}-01"
    start, _ = local_day_bounds_utc(start_str)
    end, _ = local_day_bounds_utc(end_str)
    return start, end


def local_date_str(dt: datetime) -> str:
    """Format an aware UTC (or any) datetime as YYYY-MM-DD in the app timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_app_timezone()).strftime("%Y-%m-%d")
