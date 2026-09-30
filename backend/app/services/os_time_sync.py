"""OS network time synchronization status (RDSO 18.3.6).

Interprets the clause as: the VMS host must expose and manage *real* OS time-sync
(chrony / systemd-timesyncd / Windows Time) so recording servers/devices share a
common accurate clock. Recording timestamps continue to use OS UTC.

This module never implements an NTP protocol server in Python.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SYNC_SYNCHRONIZED = "synchronized"
SYNC_NOT_SYNCHRONIZED = "not_synchronized"
SYNC_SERVICE_UNAVAILABLE = "service_unavailable"
SYNC_CONFIGURATION_REQUIRED = "configuration_required"

_ALLOWED_ACTIONS = frozenset({"resync", "enable_site_ntp_server"})


def _run_cmd(argv: list[str], *, timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as exc:
        return 1, "", str(exc)


def parse_chronyc_tracking(text: str) -> dict[str, Any]:
    """Parse `chronyc tracking` output."""
    data: dict[str, Any] = {"raw_service": "chrony"}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if k.startswith("reference id") or k.startswith("ref id"):
            data["reference"] = v
        elif k.startswith("stratum"):
            try:
                data["stratum"] = int(v.split()[0])
            except (TypeError, ValueError):
                data["stratum"] = v
        elif k.startswith("leap status"):
            data["leap_status"] = v
        elif k.startswith("system time"):
            data["system_time_offset"] = v
        elif k.startswith("last offset"):
            data["last_offset"] = v
        elif k.startswith("rms offset"):
            data["rms_offset"] = v
        elif k.startswith("update interval"):
            data["update_interval"] = v
    leap = (data.get("leap_status") or "").lower()
    ref = (data.get("reference") or "").upper()
    if "not synchron" in leap or ref in ("", "00000000", "()"):
        data["synchronized"] = False
    elif "normal" in leap or "sync" in leap:
        data["synchronized"] = True
    else:
        # chrony often reports Normal when synced
        data["synchronized"] = bool(data.get("stratum")) and int(data.get("stratum") or 99) < 16
    return data


def parse_chronyc_sources(text: str) -> list[str]:
    """Extract peer/server names from `chronyc sources -v` or `sources`."""
    peers: list[str] = []
    for line in (text or "").splitlines():
        # Lines like: ^* time.example.com ...  or  #* GPS ...
        m = re.match(r"^[\^=#\*~+-]+\s+(\S+)", line.strip())
        if m:
            peers.append(m.group(1))
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in "^#*=~+-":
            peers.append(parts[1])
    # unique preserve order
    out: list[str] = []
    for p in peers:
        if p not in out and p.lower() not in ("name/ip", "address", "sources"):
            out.append(p)
    return out


def parse_timedatectl(text: str) -> dict[str, Any]:
    """Parse `timedatectl show` or `timedatectl status`."""
    data: dict[str, Any] = {"raw_service": "systemd-timesyncd"}
    for line in (text or "").splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
        elif ":" in line:
            key, _, val = line.partition(":")
        else:
            continue
        k = key.strip().lower().replace(" ", "")
        v = val.strip()
        if k in ("ntpsynchronized", "systemclocksynchronized"):
            data["synchronized"] = v.lower() in ("yes", "true", "1")
        elif k == "ntp":
            data["ntp_enabled"] = v.lower() in ("yes", "true", "1", "active")
        elif k in ("timesyncncua", "timesyncncuaaddress"):  # uncommon
            data["ntp_server"] = v
    # status human format
    lower = (text or "").lower()
    if "system clock synchronized: yes" in lower or "ntp synchronized: yes" in lower:
        data["synchronized"] = True
    if "system clock synchronized: no" in lower or "ntp synchronized: no" in lower:
        data["synchronized"] = False
    if "ntp service: active" in lower:
        data["ntp_enabled"] = True
    if "ntp service: inactive" in lower:
        data["ntp_enabled"] = False
    return data


def parse_w32tm_status(text: str) -> dict[str, Any]:
    """Parse `w32tm /query /status`."""
    data: dict[str, Any] = {"raw_service": "w32time"}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if "source" == k or k.endswith("source"):
            # Strip ",0x8" style flags from Source: time.windows.com,0x8
            data["source"] = v.split(",", 1)[0].strip() if v else v
        elif "stratum" in k:
            try:
                data["stratum"] = int(re.findall(r"\d+", v)[0])
            except (IndexError, ValueError):
                data["stratum"] = v
        elif "last successful sync" in k or "last sync" in k:
            data["last_sync"] = v
        elif "phase offset" in k:
            data["phase_offset"] = v
        elif "poll interval" in k:
            data["poll_interval"] = v
    source = (data.get("source") or "").lower()
    if not source or "local cmos" in source or "free-running" in source:
        data["synchronized"] = False
    else:
        data["synchronized"] = True
    return data


def _clean_w32_peer_token(token: str) -> Optional[str]:
    """Strip w32tm flag suffixes (e.g. time.windows.com,0x9) and drop noise."""
    t = (token or "").strip().strip(",")
    if not t:
        return None
    # Peer lines often look like: time.windows.com,0x9
    host = t.split(",", 1)[0].strip()
    if not host:
        return None
    lower = host.lower()
    if lower in ("#peers", "peers", "(unspecified)", "unspecified", "local"):
        return None
    if re.fullmatch(r"0x[0-9a-fA-F]+", host):
        return None
    if re.fullmatch(r"\d+", host):
        return None
    return host


def parse_w32tm_peers(text: str) -> list[str]:
    """Parse `w32tm /query /peers` into hostnames (flags stripped)."""
    peers: list[str] = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        if k == "peer" or "ntp server" in k:
            cleaned = _clean_w32_peer_token(val.strip())
            if cleaned and cleaned not in peers:
                peers.append(cleaned)
    return peers


def parse_sc_query_state(text: str) -> Optional[str]:
    """Return RUNNING / STOPPED / etc from `sc query w32time`."""
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", text or "", re.I)
    if m:
        return m.group(1).upper()
    if "RUNNING" in (text or "").upper():
        return "RUNNING"
    if "STOPPED" in (text or "").upper():
        return "STOPPED"
    return None


def _linux_chrony_status() -> Optional[dict[str, Any]]:
    if not shutil.which("chronyc"):
        return None
    rc, out, err = _run_cmd(["chronyc", "tracking"])
    if rc != 0:
        return {
            "service": "chrony",
            "service_running": False,
            "sync_state": SYNC_SERVICE_UNAVAILABLE,
            "error": (err or out or "chronyc_failed")[:300],
        }
    tracking = parse_chronyc_tracking(out)
    peers: list[str] = []
    rc2, sources_out, _ = _run_cmd(["chronyc", "sources"])
    if rc2 == 0:
        peers = parse_chronyc_sources(sources_out)
    synced = bool(tracking.get("synchronized"))
    # Site NTP server capability: chrony with allow clients (detect listening UDP 123 best-effort)
    site = _detect_chrony_site_server()
    return {
        "service": "chrony",
        "service_running": True,
        "synchronized": synced,
        "sync_state": SYNC_SYNCHRONIZED if synced else SYNC_NOT_SYNCHRONIZED,
        "ntp_servers": peers,
        "source": tracking.get("reference"),
        "stratum": tracking.get("stratum"),
        "last_sync": tracking.get("update_interval"),
        "details": tracking,
        "site_time_source": site,
    }


def _detect_chrony_site_server() -> dict[str, Any]:
    """Best-effort: chrony can serve LAN clients when configured with allow."""
    conf_paths = [
        "/etc/chrony/chrony.conf",
        "/etc/chrony.conf",
    ]
    allow_lines: list[str] = []
    for path in conf_paths:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("allow ") or s.startswith("local "):
                allow_lines.append(s)
        if allow_lines:
            break
    active = any(l.startswith("allow ") for l in allow_lines)
    return {
        "capable": True,
        "active": active,
        "mode": "chrony_ntp_server" if active else "chrony_client",
        "guidance": (
            "chrony is present. To use this VMS host as the site NTP source, an administrator "
            "must explicitly allow LAN clients in chrony.conf (e.g. 'allow 192.168.0.0/16') "
            "and reload chronyd. The VMS UI can request an explicit enable action; it does not "
            "silently rewrite host NTP config."
            if not active
            else "chrony appears configured to allow clients — this host can act as site time source."
        ),
        "config_hints": allow_lines[:8],
    }


def _linux_timesyncd_status() -> Optional[dict[str, Any]]:
    if not shutil.which("timedatectl"):
        return None
    rc, out, err = _run_cmd(["timedatectl", "show"])
    if rc != 0:
        rc, out, err = _run_cmd(["timedatectl", "status"])
    if rc != 0:
        return {
            "service": "systemd-timesyncd",
            "service_running": False,
            "sync_state": SYNC_SERVICE_UNAVAILABLE,
            "error": (err or out or "timedatectl_failed")[:300],
        }
    parsed = parse_timedatectl(out)
    # timesyncd is client-only (not a full site NTP server)
    synced = bool(parsed.get("synchronized"))
    ntp_on = parsed.get("ntp_enabled")
    if ntp_on is False and not synced:
        state = SYNC_CONFIGURATION_REQUIRED
    elif synced:
        state = SYNC_SYNCHRONIZED
    else:
        state = SYNC_NOT_SYNCHRONIZED
    servers: list[str] = []
    for conf in ("/etc/systemd/timesyncd.conf", "/etc/systemd/timesyncd.conf.d"):
        # single file only
        pass
    try:
        text = open("/etc/systemd/timesyncd.conf", encoding="utf-8", errors="ignore").read()
        for line in text.splitlines():
            if line.strip().startswith("NTP="):
                servers = [p for p in line.split("=", 1)[1].split() if p]
    except OSError:
        pass
    return {
        "service": "systemd-timesyncd",
        "service_running": bool(ntp_on is not False),
        "synchronized": synced,
        "sync_state": state,
        "ntp_servers": servers,
        "source": servers[0] if servers else None,
        "details": parsed,
        "site_time_source": {
            "capable": False,
            "active": False,
            "mode": "timesyncd_client_only",
            "guidance": (
                "systemd-timesyncd is an NTP client only. For this VMS host to serve time to "
                "cameras/devices, install/configure chrony (or ntpd) as an NTP server, or point "
                "devices at a dedicated site NTP appliance."
            ),
        },
    }


def _windows_w32time_status() -> dict[str, Any]:
    sc_rc, sc_out, sc_err = _run_cmd(["sc", "query", "w32time"])
    state = parse_sc_query_state(sc_out) if sc_rc == 0 else None
    running = state == "RUNNING"
    if not running:
        return {
            "service": "w32time",
            "service_running": False,
            "service_state": state or "UNKNOWN",
            "synchronized": False,
            "sync_state": SYNC_SERVICE_UNAVAILABLE if sc_rc != 0 else SYNC_CONFIGURATION_REQUIRED,
            "error": None if running else ((sc_err or sc_out or "w32time_not_running")[:300]),
            "site_time_source": {
                "capable": True,
                "active": False,
                "mode": "w32time",
                "guidance": (
                    "Start and configure the Windows Time service (w32time), then optionally mark "
                    "this host as a reliable time source for the LAN. Use an explicit admin action "
                    "in the VMS UI — settings are not changed silently."
                ),
            },
        }

    rc, out, err = _run_cmd(["w32tm", "/query", "/status"])
    if rc != 0:
        return {
            "service": "w32time",
            "service_running": True,
            "service_state": state,
            "sync_state": SYNC_SERVICE_UNAVAILABLE,
            "error": (err or out or "w32tm_status_failed")[:300],
            "site_time_source": {
                "capable": True,
                "active": False,
                "mode": "w32time",
                "guidance": "w32time is running but status could not be queried.",
            },
        }
    parsed = parse_w32tm_status(out)
    peers: list[str] = []
    rc2, peers_out, _ = _run_cmd(["w32tm", "/query", "/peers"])
    if rc2 == 0:
        peers = parse_w32tm_peers(peers_out)
    # Detect reliable time source (AnnounceFlags) best-effort via w32tm /query /configuration
    site_active = False
    rc3, conf_out, _ = _run_cmd(["w32tm", "/query", "/configuration"])
    if rc3 == 0 and re.search(r"AnnounceFlags?\s*:\s*([0-9]+)", conf_out or "", re.I):
        m = re.search(r"AnnounceFlags?\s*:\s*([0-9]+)", conf_out or "", re.I)
        try:
            flags = int(m.group(1)) if m else 0
            # 5 = domain member reliable; non-zero announce often means advertising
            site_active = flags in (5, 10) or flags > 0 and "Type: NTP" in conf_out
        except (TypeError, ValueError):
            site_active = "AnnounceFlags: 5" in conf_out
    synced = bool(parsed.get("synchronized"))
    return {
        "service": "w32time",
        "service_running": True,
        "service_state": state,
        "synchronized": synced,
        "sync_state": SYNC_SYNCHRONIZED if synced else SYNC_NOT_SYNCHRONIZED,
        "ntp_servers": peers,
        "source": parsed.get("source"),
        "stratum": parsed.get("stratum"),
        "last_sync": parsed.get("last_sync"),
        "details": parsed,
        "site_time_source": {
            "capable": True,
            "active": bool(site_active),
            "mode": "w32time_ntp",
            "guidance": (
                "Windows Time can serve as a site time source when configured as an NTP server "
                "and marked reliable. Use the explicit admin 'enable_site_ntp_server' action."
                if not site_active
                else "Windows Time appears configured to announce as a time source."
            ),
        },
    }


def probe_os_time_sync(*, force_platform: Optional[str] = None) -> dict[str, Any]:
    """Probe the host OS time-sync service and return a normalized payload."""
    plat = (force_platform or platform.system() or "").lower()
    if plat.startswith("linux"):
        chrony = _linux_chrony_status()
        if chrony:
            return chrony
        timesyncd = _linux_timesyncd_status()
        if timesyncd:
            return timesyncd
        return {
            "service": None,
            "service_running": False,
            "synchronized": False,
            "sync_state": SYNC_CONFIGURATION_REQUIRED,
            "ntp_servers": [],
            "source": None,
            "guidance": (
                "No chrony or systemd-timesyncd detected. Install and configure one of them "
                "so this VMS host can synchronize and optionally serve site time."
            ),
            "site_time_source": {
                "capable": False,
                "active": False,
                "mode": None,
                "guidance": "Install chrony to provide both client sync and optional NTP server.",
            },
        }
    if plat.startswith("win"):
        return _windows_w32time_status()
    return {
        "service": None,
        "service_running": False,
        "synchronized": False,
        "sync_state": SYNC_SERVICE_UNAVAILABLE,
        "ntp_servers": [],
        "guidance": f"Unsupported platform for OS time-sync probe: {plat or 'unknown'}",
        "site_time_source": {"capable": False, "active": False, "mode": None},
    }


def apply_os_time_action(
    action: str,
    *,
    confirm: bool = False,
    ntp_peers: Optional[str] = None,
    force_platform: Optional[str] = None,
) -> dict[str, Any]:
    """Explicit admin OS time actions — never silent.

    Actions:
      - resync: force OS client sync (chronyc makestep / w32tm /resync)
      - enable_site_ntp_server: configure OS to advertise as NTP source (requires confirm)
    """
    act = (action or "").strip().lower()
    if act not in _ALLOWED_ACTIONS:
        return {"ok": False, "error": f"unsupported action: {action}"}
    if not confirm:
        return {
            "ok": False,
            "error": "confirm=true is required; OS time settings are never changed silently",
        }

    plat = (force_platform or platform.system() or "").lower()
    if act == "resync":
        if plat.startswith("linux"):
            if shutil.which("chronyc"):
                rc, out, err = _run_cmd(["chronyc", "makestep"])
                return {
                    "ok": rc == 0,
                    "action": act,
                    "service": "chrony",
                    "stdout": (out or "")[:500],
                    "stderr": (err or "")[:500],
                }
            if shutil.which("timedatectl"):
                rc, out, err = _run_cmd(["timedatectl", "set-ntp", "true"])
                return {
                    "ok": rc == 0,
                    "action": act,
                    "service": "systemd-timesyncd",
                    "stdout": (out or "")[:500],
                    "stderr": (err or "")[:500],
                }
            return {"ok": False, "error": "no chronyc/timedatectl available"}
        if plat.startswith("win"):
            rc, out, err = _run_cmd(["w32tm", "/resync", "/force"])
            return {
                "ok": rc == 0,
                "action": act,
                "service": "w32time",
                "stdout": (out or "")[:500],
                "stderr": (err or "")[:500],
            }
        return {"ok": False, "error": f"unsupported platform: {plat}"}

    # enable_site_ntp_server
    peers = (ntp_peers or os.getenv("VMS_NTP_PEERS") or "").strip()
    if plat.startswith("linux"):
        # Do not rewrite chrony.conf automatically — return actionable steps.
        # (Silent file edits as root are unsafe from the app process.)
        return {
            "ok": False,
            "action": act,
            "service": "chrony",
            "requires_host_admin": True,
            "error": "linux_site_ntp_requires_host_admin",
            "guidance": [
                "Edit /etc/chrony/chrony.conf (or /etc/chrony.conf) as root:",
                "  server <upstream-ntp> iburst",
                "  allow <LAN-CIDR>          # e.g. allow 192.168.0.0/16",
                "  local stratum 10         # optional when upstream unavailable",
                "Then: systemctl restart chronyd",
                "Point cameras/NVR devices at this VMS host IP as their NTP server.",
            ],
            "suggested_peers": peers or None,
        }
    if plat.startswith("win"):
        peerlist = peers or "time.windows.com"
        # Configure as NTP client of upstream, mark reliable for LAN clients.
        cmds = [
            [
                "w32tm",
                "/config",
                f"/manualpeerlist:{peerlist}",
                "/syncfromflags:manual",
                "/reliable:yes",
                "/update",
            ],
            ["net", "stop", "w32time"],
            ["net", "start", "w32time"],
            ["w32tm", "/resync", "/force"],
        ]
        results = []
        ok = True
        for argv in cmds:
            rc, out, err = _run_cmd(argv, timeout=30.0)
            results.append({"cmd": argv, "rc": rc, "stdout": out[:200], "stderr": err[:200]})
            if rc != 0 and argv[0] != "w32tm":
                # net stop may fail if already stopped — continue
                if "stop" in argv:
                    continue
                ok = False
            elif rc != 0 and argv[0] == "w32tm" and "/config" in argv:
                ok = False
        return {
            "ok": ok,
            "action": act,
            "service": "w32time",
            "peerlist": peerlist,
            "steps": results,
            "guidance": (
                "Windows Time configured as reliable. Ensure UDP/123 is allowed on the host firewall "
                "so cameras can use this VMS server as NTP."
            ),
        }
    return {"ok": False, "error": f"unsupported platform: {plat}"}
