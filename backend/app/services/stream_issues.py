"""Classify RTSP / go2rtc stream failures for diagnostics UI."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

ISSUE_LABELS: Dict[str, str] = {
    "offline": "Offline / no stream",
    "wrong_password": "Wrong password / auth failed",
    "timeout": "Timeout / unreachable",
    "codec": "Codec / format issue",
    "missing_url": "Missing RTSP URL",
    "other": "Other error",
    "online": "Online",
}


def classify_stream_error(message: Optional[str]) -> str:
    if not message or not str(message).strip():
        return "offline"
    m = str(message).lower()
    if re.search(r"401|unauthorized|wrong user|password|auth|access denied|login", m):
        return "wrong_password"
    if re.search(
        r"timeout|timed out|connection refused|econnrefused|unreachable|no route|network is unreachable|could not connect",
        m,
    ):
        return "timeout"
    if re.search(r"codec|hevc|h265|h264|unsupported|decoder|invalid data", m):
        return "codec"
    if re.search(r"missing.*url|no rtsp|not configured", m):
        return "missing_url"
    return "other"


def producer_error_text(producers: List[dict]) -> str:
    for prod in producers or []:
        if not isinstance(prod, dict):
            continue
        for key in ("error", "err", "message"):
            val = prod.get(key)
            if val:
                return str(val)
    return ""


def stream_issue_from_row(
    *,
    sub_online: bool,
    main_online: bool,
    sub_producers: List[dict],
    main_producers: List[dict],
    config_error: Optional[str] = None,
    stream_registered: bool = False,
) -> tuple[str, str]:
    """Return (issue_category, issue_message)."""
    if config_error:
        cat = classify_stream_error(config_error)
        return cat, config_error

    if sub_online or main_online:
        return "online", ""

    if stream_registered:
        return "online", ""

    err = producer_error_text(sub_producers) or producer_error_text(main_producers)
    if err:
        return classify_stream_error(err), err

    return "offline", "Stream not registered in go2rtc"


def summarize_issues(rows: List[dict]) -> Dict[str, Any]:
    counts: Dict[str, int] = {k: 0 for k in ISSUE_LABELS if k != "online"}
    by_category: Dict[str, List[dict]] = {k: [] for k in counts}

    for row in rows:
        cat = row.get("issueCategory") or "offline"
        if cat == "online":
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
