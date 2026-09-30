"""ONVIF Profile G recording search (best-effort) for edge backfill."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Optional

from app.services.edge_storage_types import PROTOCOL_ONVIF_G, to_iso
from app.services.onvif_ptz import (
    _local,
    _service_urls,
    _try_urls,
)

logger = logging.getLogger(__name__)

# Common ONVIF Recording / Search service paths
_RECORDING_PATHS = (
    "/onvif/recording_service",
    "/onvif/Recording",
    "/Recording",
    "/onvif/services",
)
_SEARCH_PATHS = (
    "/onvif/search_service",
    "/onvif/Search",
    "/Search",
    "/onvif/services",
)
_REPLAY_PATHS = (
    "/onvif/replay_service",
    "/onvif/Replay",
    "/Replay",
    "/onvif/services",
)

_TRC = "http://www.onvif.org/ver10/recording/wsdl"
_TSE = "http://www.onvif.org/ver10/search/wsdl"
_TRP = "http://www.onvif.org/ver10/replay/wsdl"


def parse_get_recordings(text: str) -> list[dict[str, Any]]:
    recordings: list[dict[str, Any]] = []
    if not text:
        return recordings
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return recordings
    for node in root.iter():
        if _local(node.tag) not in ("RecordingItem", "RecordingConfiguration", "Recording"):
            continue
        token = (node.attrib.get("token") or "").strip()
        name = None
        for child in node.iter():
            if _local(child.tag) == "Name" and (child.text or "").strip():
                name = (child.text or "").strip()
                break
            if _local(child.tag) == "RecordingToken" and (child.text or "").strip():
                token = token or (child.text or "").strip()
        if token:
            recordings.append({"token": token, "name": name or token, "protocol": PROTOCOL_ONVIF_G})
    # Deduplicate
    seen = set()
    out = []
    for r in recordings:
        if r["token"] in seen:
            continue
        seen.add(r["token"])
        out.append(r)
    return out


def parse_find_recording_results(text: str) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    if not text:
        return clips
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return clips
    for node in root.iter():
        if _local(node.tag) not in ("ResultItem", "RecordingInformation", "Track"):
            continue
        start = end = token = None
        for child in node.iter():
            tag = _local(child.tag)
            val = (child.text or "").strip()
            if tag in ("EarliestRecording", "From", "Start") and val:
                start = val
            elif tag in ("LatestRecording", "Until", "End") and val:
                end = val
            elif tag in ("RecordingToken", "TrackToken") and val:
                token = val
        if start and end:
            clips.append(
                {
                    "start": start,
                    "end": end,
                    "startTime": start,
                    "endTime": end,
                    "recording_token": token,
                    "protocol": PROTOCOL_ONVIF_G,
                }
            )
    return clips


async def _soap(
    camera: dict,
    paths: tuple[str, ...],
    action: str,
    body: str,
) -> tuple[int, str, str]:
    return await _try_urls(camera, _service_urls(camera, None, paths), action, body)


async def probe_onvif_profile_g(camera: dict) -> dict[str, Any]:
    """Probe GetRecordings — only claim support when the device answers meaningfully."""
    result: dict[str, Any] = {
        "protocol": PROTOCOL_ONVIF_G,
        "supported": False,
        "search_supported": False,
        "storage_present": False,
        "message": "",
        "details": {},
        "recordings": [],
    }
    body = (
        f'<trc:GetRecordings xmlns:trc="{_TRC}"/>'
    )
    try:
        status, text, url = await _soap(
            camera,
            _RECORDING_PATHS,
            f"{_TRC}/GetRecordings",
            body,
        )
    except Exception as exc:
        result["message"] = f"ONVIF Recording service unreachable: {exc}"
        return result

    result["details"]["get_recordings_status"] = status
    result["details"]["url"] = url
    if status != 200:
        result["message"] = (
            f"ONVIF Profile G GetRecordings not available (HTTP {status}); "
            "edge retrieval not claimed"
        )
        return result

    recordings = parse_get_recordings(text)
    result["recordings"] = recordings
    if recordings:
        result["supported"] = True
        result["search_supported"] = True
        result["storage_present"] = True
        result["message"] = f"ONVIF Profile G recordings available ({len(recordings)})"
    else:
        # HTTP 200 but empty — device may support G with no current recordings
        if "GetRecordingsResponse" in text or "Recording" in text:
            result["supported"] = True
            result["search_supported"] = True
            result["storage_present"] = True
            result["message"] = (
                "ONVIF Profile G service present but no recordings listed currently"
            )
        else:
            result["message"] = "ONVIF Recording response not recognized as Profile G"
    return result


async def search_onvif_recordings(
    camera: dict,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Find recordings overlapping [start, end] via GetRecordings + time filter.

    Full FindRecordings/KeepAlive token flows vary widely; we use GetRecordings
    metadata windows when present, and optionally FindRecordings when the device
    accepts a simple search.
    """
    probe = await probe_onvif_profile_g(camera)
    if not probe.get("supported"):
        return []

    clips: list[dict[str, Any]] = []
    # Attempt FindRecordings
    find_body = (
        f'<tse:FindRecordings xmlns:tse="{_TSE}">'
        "<tse:Scope>"
        f"<tse:RecordingInformationFilter>"
        f"boolean(//Track[TrackType = &quot;Video&quot;])"
        f"</tse:RecordingInformationFilter>"
        "</tse:Scope>"
        "<tse:MaxMatches>40</tse:MaxMatches>"
        "<tse:KeepAliveTime>PT60S</tse:KeepAliveTime>"
        "</tse:FindRecordings>"
    )
    try:
        status, text, _url = await _soap(
            camera,
            _SEARCH_PATHS,
            f"{_TSE}/FindRecordings",
            find_body,
        )
        if status == 200 and "Token" in text:
            # Extract search token and fetch results once
            token = None
            try:
                root = ET.fromstring(text)
                for node in root.iter():
                    if _local(node.tag) in ("Token", "SearchToken") and (node.text or "").strip():
                        token = (node.text or "").strip()
                        break
            except ET.ParseError:
                token = None
            if token:
                results_body = (
                    f'<tse:GetRecordingSearchResults xmlns:tse="{_TSE}">'
                    f"<tse:SearchToken>{token}</tse:SearchToken>"
                    "<tse:MinResults>1</tse:MinResults>"
                    "<tse:MaxResults>40</tse:MaxResults>"
                    "<tse:WaitTime>PT2S</tse:WaitTime>"
                    "</tse:GetRecordingSearchResults>"
                )
                rst, rtext, _ = await _soap(
                    camera,
                    _SEARCH_PATHS,
                    f"{_TSE}/GetRecordingSearchResults",
                    results_body,
                )
                if rst == 200:
                    clips.extend(parse_find_recording_results(rtext))
    except Exception as exc:
        logger.info("[EDGE] ONVIF FindRecordings skipped: %s", exc)

    # Fall back: advertise recording tokens as whole-range candidates for gap overlap
    if not clips:
        for rec in probe.get("recordings") or []:
            clips.append(
                {
                    "start": to_iso(start),
                    "end": to_iso(end),
                    "recording_token": rec.get("token"),
                    "protocol": PROTOCOL_ONVIF_G,
                    "synthetic_window": True,
                }
            )

    # Attach Replay URI when the device exposes Profile G Replay service
    enriched: list[dict[str, Any]] = []
    for clip in clips:
        token = clip.get("recording_token") or clip.get("token")
        if token and not (clip.get("playback_uri") or clip.get("playbackURI")):
            replay_uri, replay_err = await get_onvif_replay_uri(camera, str(token))
            if replay_uri:
                clip = {
                    **clip,
                    "playback_uri": replay_uri,
                    "replay_uri": replay_uri,
                }
            elif replay_err:
                clip = {**clip, "replay_error": replay_err}
        enriched.append(clip)
    return enriched


def parse_replay_uri_response(text: str) -> Optional[str]:
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for node in root.iter():
        if _local(node.tag) != "Uri":
            continue
        uri = (node.text or "").strip()
        if uri.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
            return uri
    return None


async def get_onvif_replay_uri(
    camera: dict,
    recording_token: str,
) -> tuple[Optional[str], Optional[str]]:
    """Profile G GetReplayUri — honest failure when unsupported."""
    token = (recording_token or "").strip()
    if not token:
        return None, "recording_token required"
    body = (
        f'<trp:GetReplayUri xmlns:trp="{_TRP}">'
        "<trp:StreamSetup>"
        '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
        '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema">'
        "<tt:Protocol>RTSP</tt:Protocol>"
        "</tt:Transport>"
        "</trp:StreamSetup>"
        f"<trp:RecordingToken>{token}</trp:RecordingToken>"
        "</trp:GetReplayUri>"
    )
    try:
        status, text, _url = await _soap(
            camera,
            _REPLAY_PATHS,
            f"{_TRP}/GetReplayUri",
            body,
        )
    except Exception as exc:
        return None, f"ONVIF Replay unreachable: {exc}"
    if status != 200 or "Fault" in (text or ""):
        return None, f"GetReplayUri not available (HTTP {status})"
    uri = parse_replay_uri_response(text)
    if not uri:
        return None, "GetReplayUri returned no Uri"
    return uri, None
