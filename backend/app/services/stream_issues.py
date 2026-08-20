"""Classify RTSP / go2rtc stream failures for diagnostics UI.

Offline vs checking (alert-ready):
- ``online`` — proven healthy (probe OK or live media).
- ``unchecked`` — not confirmed yet (do NOT alert).
- confirmed failure categories — health alarm or definitive fault (future
  email/WhatsApp alerts should use ``confirmedOffline`` / these categories only).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

ISSUE_LABELS: Dict[str, str] = {
    "offline": "Offline / no stream",
    "wrong_password": "Wrong password / auth failed",
    "timeout": "Timeout / unreachable",
    "codec": "Codec / format issue",
    "missing_url": "Missing / wrong RTSP path",
    "other": "Other error",
    "online": "Online",
    "unchecked": "Checking…",
}

# Confirmed failures only — never "checking" / first-strike suspects.
# Future mail/WhatsApp alerts should fire only when issueCategory is one of these
# (and the camera is active) — never for ``unchecked`` / ``online``.
CONFIRMED_OFFLINE_CATEGORIES = frozenset(
    {"offline", "wrong_password", "timeout", "codec", "missing_url", "other"}
)

# One sighting is enough — do not wait for health strikes.
DEFINITIVE_OFFLINE_CATEGORIES = frozenset({"wrong_password", "missing_url", "codec"})


def is_confirmed_offline(issue_category: Optional[str]) -> bool:
    """True when category is alertable offline (not checking/online)."""
    return (issue_category or "") in CONFIRMED_OFFLINE_CATEGORIES


def is_checking(issue_category: Optional[str]) -> bool:
    return (issue_category or "") in ("", "unchecked")


def is_definitive_issue(category: str, message: str = "") -> bool:
    """Faults that confirm offline immediately (auth/path/codec/dead host)."""
    if category in DEFINITIVE_OFFLINE_CATEGORIES:
        return True
    m = (message or "").lower()
    if re.search(
        r"rtsp port \d+ closed|port closed or unreachable|destination host unreachable|no route to host",
        m,
    ):
        return True
    return False


def classify_stream_error(message: Optional[str]) -> str:
    if not message or not str(message).strip():
        return "offline"
    m = str(message).lower()
    # go2rtc often truncates auth failures to "streams: wrong" (wrong user/pass).
    if re.search(
        r"401|unauthorized|wrong user|password|auth|access denied|login|streams:\s*wrong\b",
        m,
    ):
        return "wrong_password"
    if re.search(
        r"timeout|timed out|connection refused|econnrefused|unreachable|no route|network is unreachable|could not connect",
        m,
    ):
        return "timeout"
    # Hikvision concurrent session limit — not a wrong path; free other RTSP clients.
    if re.search(r"\b453\b|not enough bandwidth", m):
        return "other"
    # RTSP DESCRIBE ok but SETUP fails — wrong brand path (common on Sparsh/HSD).
    if re.search(r"eof.*setup|setup.*eof|response on setup|rtsp.*setup", m):
        return "missing_url"
    if re.search(r"codec|hevc|h265|h264|unsupported|decoder|invalid data", m):
        return "codec"
    if re.search(r"missing.*url|no rtsp|not configured|not registered", m):
        return "missing_url"
    return "other"


def producer_error_text(producers: List[dict]) -> str:
    for prod in producers or []:
        if not isinstance(prod, dict):
            continue
        for key in ("error", "err", "message"):
            val = prod.get(key)
            if val and str(val).strip():
                text = str(val).strip()
                # Ignore empty/generic placeholders — real go2rtc errors mention streams/rtsp.
                if key == "message" and not re.search(
                    r"stream|rtsp|401|auth|timeout|eof|setup|wrong|codec|refused|unreachable|453|bandwidth",
                    text,
                    re.I,
                ):
                    continue
                return text
    return ""


def producers_streaming(producers: List[dict]) -> bool:
    """True only when go2rtc reports an active media pipeline (not just an RTSP attempt)."""
    if not producers:
        return False
    if producer_error_text(producers):
        return False
    for prod in producers:
        if not isinstance(prod, dict):
            continue
        medias = prod.get("medias") or []
        receivers = prod.get("receivers") or []
        if medias or receivers:
            return True
        if prod.get("bytes_recv") or prod.get("bytes") or prod.get("recv"):
            return True
    return False


def stream_payload_for_src(data: Any, src: str) -> Dict[str, Any]:
    """Unwrap go2rtc /api/streams JSON for one src (do not log URLs)."""
    if not isinstance(data, dict):
        return {}
    inner = data.get(src)
    if isinstance(inner, dict):
        return inner
    if "producers" in data or "consumers" in data:
        return data
    return {}


def stream_has_active_viewers(info: Optional[dict]) -> bool:
    """True when a browser (or another client) is already pulling this stream."""
    if not isinstance(info, dict):
        return False
    if info.get("consumers"):
        return True
    return producers_streaming(info.get("producers") or [])


def stream_issue_from_row(
    *,
    sub_online: bool,
    main_online: bool,
    sub_producers: List[dict],
    main_producers: List[dict],
    config_error: Optional[str] = None,
    stream_registered: bool = False,
    worker_running: bool = True,
    worker_id: int = 1,
    health: Optional[dict] = None,
) -> tuple[str, str]:
    """Return (issue_category, issue_message).

    Confirmed offline categories are alert-ready. Transient RTSP/connect noise
    stays ``unchecked`` until the health probe alarms (multi-strike) or a
    definitive fault is seen.
    """
    if not worker_running:
        return "offline", f"go2rtc worker {worker_id} not running"

    # Live media wins — camera is up regardless of a stale probe failure.
    if producers_streaming(sub_producers) or producers_streaming(main_producers):
        return "online", ""

    if health:
        if health.get("ok"):
            return "online", ""
        # First-strike / in-progress probe — never alertable.
        if health.get("suspect") and not health.get("alarm"):
            return "unchecked", (health.get("message") or "").strip()
        if health.get("alarm"):
            cat = health.get("category") or "offline"
            msg = (health.get("message") or "").strip()
            if not msg:
                msg = ISSUE_LABELS.get(cat, "Stream probe failed")
            return cat, msg

    err = (config_error or "").strip() or producer_error_text(sub_producers) or producer_error_text(
        main_producers
    )
    if err:
        cat = classify_stream_error(err)
        # Auth / wrong path / codec confirm immediately; timeouts stay Checking.
        if is_definitive_issue(cat, err):
            return cat, err
        return "unchecked", err

    if stream_registered:
        # Registered but idle and no probe yet — wait for health scan.
        return "unchecked", ""

    # Missing from go2rtc after restart/sync race — Checking, not alert Offline.
    return "unchecked", "Stream not registered in go2rtc"


def summarize_issues(rows: List[dict]) -> Dict[str, Any]:
    """Summarize confirmed issues only (excludes online + checking)."""
    counts: Dict[str, int] = {k: 0 for k in ISSUE_LABELS if k not in ("online", "unchecked")}
    by_category: Dict[str, List[dict]] = {k: [] for k in counts}

    for row in rows:
        cat = row.get("issueCategory") or "offline"
        if cat in ("online", "unchecked"):
            continue
        # Prefer explicit flag when present (future alert pipeline).
        if row.get("confirmedOffline") is False:
            continue
        counts[cat] = counts.get(cat, 0) + 1
        by_category.setdefault(cat, []).append(
            {
                "cameraId": row.get("cameraId"),
                "cameraName": row.get("cameraName"),
                "message": row.get("issueMessage") or ISSUE_LABELS.get(cat, cat),
                "site": row.get("site"),
                "building": row.get("building"),
                "floor": row.get("floor"),
            }
        )

    return {
        "counts": counts,
        "byCategory": by_category,
        "totalWithIssues": sum(counts.values()),
    }
