"""Recording env config — defaults: main stream 101 (evidence quality), 5 min HLS segments."""

import os


def is_recording_engine_enabled() -> bool:
    """Master create-new-recordings flag. Default off (storage not available).

    RECORDING_ENABLED=false must not block playback of existing recordings.
    """
    return os.getenv("RECORDING_ENABLED", "false").strip().lower() in ("1", "true", "yes")


class RecordingEngineDisabled(RuntimeError):
    """Attempted to start a recorder while RECORDING_ENABLED is false."""


RECORDING_STREAM = os.getenv("RECORDING_STREAM", "main").strip().lower()
RECORDING_SEGMENT_SECONDS = os.getenv("RECORDING_HLS_SEGMENT_SECONDS", "300")
RECORDING_LIST_SIZE = int(os.getenv("RECORDING_HLS_LIST_SIZE", "0"))

# Retention: HOURS wins over DAYS if set
_retention_hours = os.getenv("RECORDING_RETENTION_HOURS", "").strip()
_retention_days = os.getenv("RECORDING_RETENTION_DAYS", "").strip()

if _retention_hours:
    RECORDING_RETENTION_SECONDS = float(_retention_hours) * 3600
elif _retention_days:
    RECORDING_RETENTION_SECONDS = float(_retention_days) * 86400
else:
    RECORDING_RETENTION_SECONDS = 15 * 86400  # 15 days default

STATUS_LOG_INTERVAL_SECONDS = int(os.getenv("RECORDING_STATUS_LOG_SECONDS", "60"))
RETENTION_PASS_INTERVAL_SECONDS = int(os.getenv("RECORDING_RETENTION_PASS_SECONDS", "300"))


def _env_retention_days() -> float:
    if _retention_hours:
        return float(_retention_hours) / 24
    if _retention_days:
        return float(_retention_days)
    return 15.0


def get_retention_policy() -> dict:
    """Current retention window for API / UI."""
    from app.services.storage_settings_store import get_effective_retention_days, get_effective_retention_seconds

    days = get_effective_retention_days()
    seconds = get_effective_retention_seconds()
    env_days = _env_retention_days()
    source = "ui" if abs(days - env_days) > 0.001 else ("days" if _retention_days else ("hours" if _retention_hours else "default"))
    label = f"{days:g} day(s)"
    if source == "default" and not _retention_days and not _retention_hours:
        label = "15 days (default)"
    return {
        "source": source,
        "label": label,
        "retention_seconds": int(seconds),
        "retention_hours": round(seconds / 3600, 2),
        "retention_days": round(days, 3),
        "pass_interval_seconds": RETENTION_PASS_INTERVAL_SECONDS,
        "editable": True,
    }


def resolve_recording_stream_choice(camera_doc: dict) -> str:
    """Per-camera main/sub choice; RECORDING_STREAM env is fallback when unset."""
    raw = (camera_doc.get("recording_channel") or "").strip().lower()
    if raw == "main":
        return "main"
    if raw == "sub":
        return "sub"
    main_ch = str(camera_doc.get("main_channel") or "101").strip()
    sub_ch = str(camera_doc.get("sub_channel") or "102").strip()
    if raw and raw == main_ch:
        return "main"
    if raw and raw == sub_ch:
        return "sub"
    return "main" if RECORDING_STREAM == "main" else "sub"


def recording_stream_profile_for_camera(camera_doc: dict) -> str:
    stream = resolve_recording_stream_choice(camera_doc)
    if stream == "main":
        return "main/101 HEVC copy (evidence quality)"
    return "sub/102 HEVC copy (~256-512 Kbps, not recommended for evidence)"


def recording_stream_profile() -> str:
    if RECORDING_STREAM == "main":
        return "main/101 HEVC copy (evidence quality)"
    return "sub/102 HEVC copy (~256-512 Kbps, not recommended for evidence)"


def get_recording_stream_info() -> dict:
    """API/UI payload for current recording stream selection."""
    is_main = RECORDING_STREAM == "main"
    return {
        "recording_stream": RECORDING_STREAM,
        "channel": "101" if is_main else "102",
        "quality_label": (
            "Main Stream / Evidence Quality"
            if is_main
            else "Substream / Low Quality"
        ),
        "substream_warning": not is_main,
        "stream_profile": recording_stream_profile(),
        "transcode": False,
        "codec_mode": "copy",
    }


def resolve_recording_rtsp_url(camera_doc: dict, urls: dict) -> tuple[str | None, str]:
    stream = resolve_recording_stream_choice(camera_doc)
    via_go2rtc = os.getenv("RECORDING_VIA_GO2RTC", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if via_go2rtc:
        from app.services.go2rtc_service import GO2RTC_ENABLED, local_recording_rtsp_url
        from app.services.camera_uid import make_camera_uid

        if GO2RTC_ENABLED:
            uid = (
                camera_doc.get("camera_uid")
                or make_camera_uid(camera_doc.get("ip_address") or "")
                or str(camera_doc.get("_id") or "")
            )
            if uid:
                label = "main/101 via go2rtc" if stream == "main" else "sub/102 via go2rtc"
                worker_id = None
                from app.services.go2rtc_workers import WORKERS_ENABLED, normalize_worker_id

                if WORKERS_ENABLED:
                    worker_id = normalize_worker_id(camera_doc.get("worker_id")) or 1
                return local_recording_rtsp_url(uid, stream, worker_id=worker_id), label

    if stream == "main":
        url = camera_doc.get("main_rtsp_url") or urls.get("main_rtsp_url")
        return url, "main/101"
    url = camera_doc.get("sub_rtsp_url") or urls.get("sub_rtsp_url")
    return url, "sub/102"
