"""ONVIF WS-Discovery and routed-subnet camera discovery (RDSO 18.1.9)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from wsdiscovery import WSDiscovery

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_TIMEOUT = 5
MAX_SCAN_PREFIX_LEN = 24  # /24 or smaller (more specific)
MAX_SCAN_HOSTS = 256
SUBNET_SCAN_CONCURRENCY = int(os.getenv("DISCOVERY_SUBNET_CONCURRENCY", "32"))
RTSP_PROBE_TIMEOUT = float(os.getenv("DISCOVERY_RTSP_TIMEOUT", "1.5"))
SUBNET_SCAN_OVERALL_TIMEOUT = int(os.getenv("DISCOVERY_SUBNET_OVERALL_TIMEOUT", "90"))
ONVIF_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=2.5, connect=1.5)
RTSP_PORT = 554

_ONVIF_DEVICE_PATHS = (
    "/onvif/device_service",
    "/onvif/device",
    "/onvif/Device",
)

# ONVIF scope URI prefixes (see ONVIF Core Specification).
_SCOPE_NAME = "onvif://www.onvif.org/name/"
_SCOPE_HARDWARE = "onvif://www.onvif.org/hardware/"
_SCOPE_MANUFACTURER = "onvif://www.onvif.org/manufacturer/"
_SCOPE_LOCATION = "onvif://www.onvif.org/location/"


def extract_ip_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    return (parsed.hostname or "").strip()


def _scope_value(scope: str, prefix: str) -> Optional[str]:
    text = (scope or "").strip()
    if not text.lower().startswith(prefix.lower()):
        return None
    value = text[len(prefix) :].strip()
    return value or None


def parse_onvif_scopes(scopes: Iterable[str]) -> Dict[str, str]:
    """Extract display fields from ONVIF WS-Discovery scopes."""
    name = ""
    model = ""
    manufacturer = ""

    for raw in scopes or []:
        scope = str(raw).strip()
        if not scope:
            continue
        if val := _scope_value(scope, _SCOPE_NAME):
            name = val
        elif val := _scope_value(scope, _SCOPE_MANUFACTURER):
            manufacturer = val
        elif val := _scope_value(scope, _SCOPE_HARDWARE):
            if not model:
                model = val
            if not manufacturer and " " in val:
                manufacturer = val.split(None, 1)[0]

    return {
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
    }


def is_onvif_network_video_transmitter(types: Iterable[Any], scopes: Iterable[str], xaddrs: Iterable[str]) -> bool:
    """True when the WS-Discovery service looks like an ONVIF camera/NVT."""
    type_text = " ".join(str(t) for t in (types or [])).lower()
    if "networkvideotransmitter" in type_text:
        return True

    scope_text = " ".join(str(s) for s in (scopes or [])).lower()
    if "onvif://www.onvif.org" in scope_text:
        return True

    for addr in xaddrs or []:
        path = (urlparse(str(addr)).path or "").lower()
        if "/onvif" in path or "device_service" in path:
            return True

    return False


def _pick_onvif_endpoint(xaddrs: List[str]) -> str:
    if not xaddrs:
        return ""
    for addr in xaddrs:
        lower = addr.lower()
        if "device_service" in lower or "/onvif/" in lower:
            return addr
    return xaddrs[0]


def parse_wsdiscovery_service(service: Any) -> Optional[Dict[str, str]]:
    types = service.getTypes() if hasattr(service, "getTypes") else []
    xaddrs = list(service.getXAddrs() or []) if hasattr(service, "getXAddrs") else []
    scopes = list(service.getScopes() or []) if hasattr(service, "getScopes") else []

    if not is_onvif_network_video_transmitter(types, scopes, xaddrs):
        return None

    endpoint = _pick_onvif_endpoint(xaddrs)
    ip = extract_ip_from_url(endpoint) or extract_ip_from_url(xaddrs[0] if xaddrs else "")
    if not ip:
        return None

    meta = parse_onvif_scopes(scopes)
    display_name = meta["name"] or f"ONVIF Camera at {ip}"

    return {
        "ip_address": ip,
        "manufacturer": meta["manufacturer"],
        "model": meta["model"],
        "onvif_endpoint": endpoint,
        "name": display_name,
    }


def dedupe_discovered(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep one row per IP; prefer entries with richer metadata."""
    by_ip: Dict[str, Dict[str, str]] = {}
    for row in rows:
        ip = (row.get("ip_address") or "").strip()
        if not ip:
            continue
        existing = by_ip.get(ip)
        if not existing:
            by_ip[ip] = row
            continue
        existing_score = sum(1 for k in ("manufacturer", "model", "onvif_endpoint", "name") if existing.get(k))
        row_score = sum(1 for k in ("manufacturer", "model", "onvif_endpoint", "name") if row.get(k))
        if row_score > existing_score:
            by_ip[ip] = row
    return sorted(by_ip.values(), key=lambda r: r.get("ip_address") or "")


def mark_discovery_status(
    rows: List[Dict[str, str]],
    configured_ips: Set[str],
) -> List[Dict[str, Any]]:
    """Annotate each row with status: new | already_added."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        ip = (row.get("ip_address") or "").strip()
        status = "already_added" if ip in configured_ips else "new"
        out.append({**row, "status": status})
    return out


def _run_wsdiscovery(timeout: int) -> List[Any]:
    wsd = WSDiscovery()
    wsd.start()
    try:
        return wsd.searchServices(timeout=timeout)
    finally:
        wsd.stop()


async def discover_onvif_cameras(
    *,
    configured_ips: Set[str],
    timeout: int = DEFAULT_DISCOVERY_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    Run ONVIF WS-Discovery multicast probe and compare results to configured IPs.
    Does not scan IP ranges or probe RTSP ports.
    """
    try:
        services = await asyncio.to_thread(_run_wsdiscovery, timeout)
    except Exception as exc:
        logger.warning("[camera_discovery] WS-Discovery failed: %s", exc)
        services = []

    parsed: List[Dict[str, str]] = []
    for service in services or []:
        row = parse_wsdiscovery_service(service)
        if row:
            parsed.append(row)

    deduped = dedupe_discovered(parsed)
    return mark_discovery_status(deduped, configured_ips)


def normalize_discovery_ip(ip: str) -> str:
    return (ip or "").strip().lower()


def validate_scan_cidr(cidr: str) -> ipaddress.IPv4Network:
    """Accept a valid IPv4 CIDR; reject invalid or ranges larger than /24."""
    text = (cidr or "").strip()
    if not text:
        raise ValueError("Subnet CIDR is required")
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid subnet CIDR: {text}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Only IPv4 subnets are supported")
    if network.prefixlen < MAX_SCAN_PREFIX_LEN:
        raise ValueError(
            f"Subnet too large ({network.with_prefixlen}). "
            f"Maximum scan range is /{MAX_SCAN_PREFIX_LEN} ({MAX_SCAN_HOSTS} hosts)."
        )
    if network.num_addresses > MAX_SCAN_HOSTS + 2:
        raise ValueError(
            f"Subnet has too many addresses; maximum allowed is /{MAX_SCAN_PREFIX_LEN}."
        )
    return network


def subnets_from_camera_ips(ips: Iterable[str]) -> List[str]:
    """Derive unique /24 subnet options from configured camera IPs."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in ips:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            continue
        if not isinstance(addr, ipaddress.IPv4Address):
            continue
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
        key = str(net)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return sorted(out, key=lambda s: tuple(int(p) for p in s.split("/")[0].split(".")))


def _local_xml(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_device_information_xml(body: str) -> Dict[str, str]:
    out = {"manufacturer": "", "model": "", "onvif_endpoint": ""}
    if not body or "<" not in body:
        return out
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return out
    for el in root.iter():
        name = _local_xml(el.tag)
        text = (el.text or "").strip()
        if not text:
            continue
        if name == "Manufacturer" and not out["manufacturer"]:
            out["manufacturer"] = text
        elif name == "Model" and not out["model"]:
            out["model"] = text
    return out


def _get_device_information_envelope() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        "<s:Body>"
        '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>'
        "</s:Body></s:Envelope>"
    )


async def probe_onvif_metadata(ip: str) -> Dict[str, str]:
    """Best-effort ONVIF GetDeviceInformation without credentials."""
    envelope = _get_device_information_envelope()
    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    for port in (80, 8080, 8000):
        for path in _ONVIF_DEVICE_PATHS:
            url = f"http://{ip}:{port}{path}"
            try:
                async with aiohttp.ClientSession(timeout=ONVIF_PROBE_TIMEOUT) as session:
                    async with session.post(url, data=envelope, headers=headers) as resp:
                        body = await resp.text(errors="replace")
                        if resp.status not in (200, 400, 401, 403, 500):
                            continue
                        meta = _parse_device_information_xml(body)
                        if meta["manufacturer"] or meta["model"]:
                            meta["onvif_endpoint"] = url
                            return meta
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
    return {"manufacturer": "", "model": "", "onvif_endpoint": ""}


async def _probe_rtsp_open(ip: str, port: int = RTSP_PORT, timeout: float = RTSP_PROBE_TIMEOUT) -> bool:
    try:
        fut = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        del reader
        return True
    except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
        return False


async def scan_subnet_for_cameras(
    cidr: str,
    *,
    configured_ips: Set[str],
    cancel_event: Optional[asyncio.Event] = None,
) -> List[Dict[str, Any]]:
    """
    Probe RTSP port 554 on each host in a single subnet with bounded concurrency.
    No password guessing or credential brute force.
    """
    network = validate_scan_cidr(cidr)
    hosts = [str(ip) for ip in network.hosts()]
    if not hosts:
        return []

    sem = asyncio.Semaphore(max(1, SUBNET_SCAN_CONCURRENCY))
    found: List[Dict[str, str]] = []

    async def check_host(ip: str) -> None:
        if cancel_event and cancel_event.is_set():
            return
        async with sem:
            if cancel_event and cancel_event.is_set():
                return
            if not await _probe_rtsp_open(ip):
                return
            meta = await probe_onvif_metadata(ip)
            name = meta.get("model") or meta.get("manufacturer") or f"Camera at {ip}"
            if meta.get("manufacturer") and meta.get("model"):
                name = f"{meta['manufacturer']} {meta['model']}"
            found.append(
                {
                    "ip_address": ip,
                    "name": name,
                    "manufacturer": meta.get("manufacturer") or "",
                    "model": meta.get("model") or "",
                    "onvif_endpoint": meta.get("onvif_endpoint") or "",
                    "discovery_source": "subnet_scan",
                }
            )

    async def run_scan() -> None:
        await asyncio.gather(*(check_host(ip) for ip in hosts), return_exceptions=True)

    try:
        await asyncio.wait_for(run_scan(), timeout=SUBNET_SCAN_OVERALL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("[camera_discovery] Subnet scan timed out for %s", cidr)
        if cancel_event:
            cancel_event.set()

    deduped = dedupe_discovered(found)
    return mark_discovery_status(deduped, configured_ips)


async def discover_cameras_full(
    *,
    configured_ips: Set[str],
    ws_timeout: int = DEFAULT_DISCOVERY_TIMEOUT,
    subnet: Optional[str] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> Dict[str, Any]:
    """WS-Discovery (unchanged) plus optional single-subnet routed fallback."""
    ws_results = await discover_onvif_cameras(
        configured_ips=configured_ips,
        timeout=ws_timeout,
    )
    for row in ws_results:
        row.setdefault("discovery_source", "ws_discovery")

    subnet_results: List[Dict[str, Any]] = []
    subnet_scanned: Optional[str] = None
    if subnet and (subnet := subnet.strip()):
        subnet_scanned = validate_scan_cidr(subnet).with_prefixlen
        subnet_results = await scan_subnet_for_cameras(
            str(subnet_scanned),
            configured_ips=configured_ips,
            cancel_event=cancel_event,
        )

    merged = dedupe_discovered([*ws_results, *subnet_results])
    merged = mark_discovery_status(merged, configured_ips)
    return {
        "discovered": merged,
        "ws_discovery_count": len(ws_results),
        "subnet_scan_count": len(subnet_results),
        "subnet_scanned": str(subnet_scanned) if subnet_scanned else None,
    }
