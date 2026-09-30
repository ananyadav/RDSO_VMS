"""Hikvision ISAPI ContentMgmt search/download for edge recordings."""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiohttp

from app.services.edge_storage_types import PROTOCOL_ISAPI, to_iso
from app.services.hikvision_ptz import (
    _credentials,
    _isapi,
    _isapi_base,
)

logger = logging.getLogger(__name__)


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _track_id(camera: dict) -> str:
    for key in ("recording_channel", "main_channel", "edge_track_id"):
        val = camera.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return "101"


def build_cmsearch_xml(
    *,
    start: datetime,
    end: datetime,
    track_id: str = "101",
    max_results: int = 100,
    search_id: Optional[str] = None,
) -> bytes:
    sid = search_id or str(uuid.uuid4())
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<CMSearchDescription>"
        f"<searchID>{sid}</searchID>"
        "<trackList>"
        f"<trackID>{track_id}</trackID>"
        "</trackList>"
        "<timeSpanList><timeSpan>"
        f"<startTime>{to_iso(start)}</startTime>"
        f"<endTime>{to_iso(end)}</endTime>"
        "</timeSpan></timeSpanList>"
        f"<maxResults>{int(max_results)}</maxResults>"
        "<searchResultPostion>0</searchResultPostion>"
        "<metadataList>"
        "<metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor>"
        "</metadataList>"
        "</CMSearchDescription>"
    )
    return body.encode("utf-8")


def parse_cmsearch_response(text: str) -> list[dict[str, Any]]:
    """Parse ISAPI CMSearchResult into clip dicts."""
    clips: list[dict[str, Any]] = []
    if not (text or "").strip():
        return clips
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return clips

    for node in root.iter():
        if _local(node.tag) != "searchMatchItem":
            continue
        start = end = playback_uri = track = None
        for child in node.iter():
            tag = _local(child.tag)
            val = (child.text or "").strip()
            if tag == "startTime" and val:
                start = val
            elif tag == "endTime" and val:
                end = val
            elif tag in ("playbackURI", "playbackURI") and val:
                playback_uri = val
            elif tag == "trackID" and val:
                track = val
        # Also attribute-style / nested timeSpan
        if start is None or end is None:
            for child in node:
                if _local(child.tag) == "timeSpan":
                    for t in child:
                        tt = _local(t.tag)
                        tv = (t.text or "").strip()
                        if tt == "startTime":
                            start = tv
                        elif tt == "endTime":
                            end = tv
                if _local(child.tag) == "mediaSegmentDescriptor":
                    for t in child:
                        if _local(t.tag) == "playbackURI":
                            playback_uri = (t.text or "").strip()
        if start and end:
            clips.append(
                {
                    "start": start,
                    "end": end,
                    "startTime": start,
                    "endTime": end,
                    "playback_uri": playback_uri,
                    "track_id": track,
                    "protocol": PROTOCOL_ISAPI,
                }
            )
    return clips


def parse_storage_status(text: str) -> dict[str, Any]:
    """Best-effort parse of ISAPI storage / HDD list for SD presence."""
    info: dict[str, Any] = {
        "raw_ok": bool(text and text.strip()),
        "has_storage": False,
        "volumes": [],
    }
    if not text:
        return info
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Some firmwares return HTML/JSON errors
        lower = text.lower()
        info["has_storage"] = "hdd" in lower or "sd" in lower or "nas" in lower
        return info

    for node in root.iter():
        tag = _local(node.tag).lower()
        if tag in ("hdd", "storagelist", "nas", "workstatus", "status"):
            pass
        if tag in ("hdd", "nas", "workdirectory"):
            vol: dict[str, Any] = {"type": tag}
            for child in list(node)[:20]:
                vol[_local(child.tag)] = (child.text or "").strip()
            if vol:
                info["volumes"].append(vol)
                info["has_storage"] = True
        if tag == "status" and (node.text or "").strip().lower() in (
            "ok",
            "normal",
            "online",
        ):
            info["has_storage"] = True
    if not info["has_storage"]:
        # Any capacity element implies storage hardware is reported
        for node in root.iter():
            if _local(node.tag).lower() in ("capacity", "freespace", "property"):
                info["has_storage"] = True
                break
    return info


async def probe_isapi_edge_storage(camera: dict) -> dict[str, Any]:
    """Probe whether ContentMgmt / storage endpoints respond."""
    result: dict[str, Any] = {
        "protocol": PROTOCOL_ISAPI,
        "supported": False,
        "search_supported": False,
        "storage_present": False,
        "message": "",
        "details": {},
    }
    try:
        st, text = await _isapi(camera, "GET", "/ISAPI/ContentMgmt/InputProxy/channels")
    except Exception as exc:
        result["message"] = f"ISAPI unreachable: {exc}"
        return result

    # ContentMgmt search is the key capability; storage list is supportive.
    storage_paths = (
        "/ISAPI/ContentMgmt/Storage",
        "/ISAPI/ContentMgmt/storage",
        "/ISAPI/System/Video/inputs/channels",
    )
    storage_text = ""
    storage_status = 0
    for path in storage_paths:
        try:
            storage_status, storage_text = await _isapi(camera, "GET", path)
        except Exception:
            continue
        if storage_status == 200 and storage_text:
            break

    parsed = parse_storage_status(storage_text) if storage_text else {}
    result["details"]["storage_http_status"] = storage_status
    result["details"]["content_mgmt_channels_status"] = st
    result["storage_present"] = bool(parsed.get("has_storage")) or st in (200, 400)

    # Probe search with a tiny window (device may return 200 empty or 4xx if unsupported)
    from datetime import timedelta, timezone

    now = datetime.now(timezone.utc)
    xml = build_cmsearch_xml(
        start=now - timedelta(minutes=1),
        end=now,
        track_id=_track_id(camera),
        max_results=1,
    )
    try:
        sst, stext = await _isapi(
            camera, "POST", "/ISAPI/ContentMgmt/search", body=xml
        )
    except Exception as exc:
        result["message"] = f"ContentMgmt search failed: {exc}"
        return result

    result["details"]["search_http_status"] = sst
    if sst == 200:
        result["search_supported"] = True
        result["supported"] = True
        result["message"] = "Hikvision ISAPI ContentMgmt search available"
    elif sst in (401, 403):
        result["message"] = "ISAPI authentication failed for ContentMgmt search"
    elif sst == 404:
        result["message"] = "Camera does not expose ISAPI ContentMgmt search (no edge retrieval)"
    else:
        # Some cameras return 4xx for empty ranges but still support search
        if "CMSearchResult" in (stext or "") or "searchMatchItem" in (stext or ""):
            result["search_supported"] = True
            result["supported"] = True
            result["message"] = "Hikvision ISAPI ContentMgmt search available"
        else:
            result["message"] = (
                f"ISAPI ContentMgmt search not usable (HTTP {sst}); "
                "edge backfill not claimed for this camera"
            )
    return result


async def search_isapi_recordings(
    camera: dict,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    xml = build_cmsearch_xml(start=start, end=end, track_id=_track_id(camera))
    status, text = await _isapi(camera, "POST", "/ISAPI/ContentMgmt/search", body=xml)
    if status != 200:
        logger.info("[EDGE] ISAPI search HTTP %s: %s", status, (text or "")[:200])
        return []
    return parse_cmsearch_response(text)


async def download_isapi_playback(
    camera: dict,
    playback_uri: str,
    dest: Path,
    *,
    timeout_s: float = 120.0,
) -> int:
    """Download edge media referenced by playbackURI into dest. Returns bytes written."""
    from app.services.http_digest import _build_digest_header, _parse_digest_challenge

    dest.parent.mkdir(parents=True, exist_ok=True)
    uri = (playback_uri or "").strip()
    if not uri:
        raise ValueError("Missing playback URI")
    if uri.lower().startswith("rtsp://"):
        raise ValueError("rtsp_playback_uri")

    if uri.startswith("/"):
        url = f"{_isapi_base(camera)}{uri}"
    elif uri.lower().startswith("http://") or uri.lower().startswith("https://"):
        url = uri
    else:
        from urllib.parse import quote

        url = (
            f"{_isapi_base(camera)}/ISAPI/ContentMgmt/download"
            f"?playbackURI={quote(uri, safe='')}"
        )

    username, password = _credentials(camera)
    timeout = aiohttp.ClientTimeout(total=timeout_s, connect=10)
    connector = aiohttp.TCPConnector(ssl=False)
    written = 0

    async with aiohttp.ClientSession(connector=connector) as session:
        headers: dict[str, str] = {}
        async with session.get(url, timeout=timeout, ssl=False) as probe:
            if probe.status == 401:
                challenge = _parse_digest_challenge(
                    probe.headers.get("WWW-Authenticate", "")
                )
                headers["Authorization"] = _build_digest_header(
                    method="GET",
                    url=url,
                    username=username,
                    password=password,
                    challenge=challenge,
                )
            elif probe.status == 200:
                with open(dest, "wb") as fh:
                    async for chunk in probe.content.iter_chunked(64 * 1024):
                        fh.write(chunk)
                        written += len(chunk)
                return written
            else:
                body = await probe.text()
                raise RuntimeError(f"ISAPI download HTTP {probe.status}: {body[:200]}")

        async with session.get(
            url, headers=headers, timeout=timeout, ssl=False
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"ISAPI download HTTP {resp.status}: {body[:200]}")
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    fh.write(chunk)
                    written += len(chunk)
    return written
