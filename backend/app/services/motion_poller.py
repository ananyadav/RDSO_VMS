"""Background poller for motion/activity hold expiry + optional ISAPI probes."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET

from app.services.motion_recording_types import DEFAULT_POLL_SECONDS

logger = logging.getLogger(__name__)

_poll_task: asyncio.Task | None = None


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def parse_isapi_motion_active(text: str) -> bool | None:
    """Best-effort: True/False if XML clearly indicates active alarm; else None."""
    if not text:
        return None
    lower = text.lower()
    # Common firmware markers
    if any(
        x in lower
        for x in (
            "<ismotion>true</ismotion>",
            "<motiondetection>true</motiondetection>",
            "<active>true</active>",
            "<alarm>true</alarm>",
            "state>active",
        )
    ):
        return True
    if any(
        x in lower
        for x in (
            "<ismotion>false</ismotion>",
            "<active>false</active>",
            "<alarm>false</alarm>",
            "state>inactive",
        )
    ):
        return False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for node in root.iter():
        tag = _local(node.tag).lower()
        val = (node.text or "").strip().lower()
        if tag in ("ismotion", "active", "alarm", "triggered") and val in (
            "true",
            "1",
            "yes",
            "false",
            "0",
            "no",
        ):
            return val in ("true", "1", "yes")
    return None


async def _probe_camera_activity(camera: dict) -> bool | None:
    """Optional ISAPI live activity probe — returns None when unknown (no fake MD)."""
    from app.services.hikvision_ptz import _isapi
    from app.services.motion_capability import _prefer_isapi

    if not _prefer_isapi(camera):
        return None
    mr = camera.get("motion_recording") or {}
    src = str(mr.get("motion_source") or "auto").lower()
    if src not in ("auto", "isapi"):
        return None
    paths = (
        "/ISAPI/System/Video/inputs/channels/1/motionDetection",
        "/ISAPI/Event/triggers",
    )
    for path in paths:
        try:
            status, text = await _isapi(camera, "GET", path)
        except Exception:
            continue
        if status != 200:
            continue
        active = parse_isapi_motion_active(text or "")
        if active is not None:
            return active
    return None


async def _poll_once() -> None:
    from app.services.camera_event_inputs import (
        emit_digital_input_alarm_if_rising,
        emit_motion_alarm_if_rising,
        list_cameras_with_source_rules,
        probe_digital_input_active,
        probe_digital_input_capability,
    )
    from app.services.motion_recording_config import list_motion_enabled_cameras
    from app.services.motion_recording_controller import (
        notify_motion_activity,
        refresh_camera_config,
        tick_idle_hold,
    )

    cameras = await list_motion_enabled_cameras()
    seen: set[str] = set()
    for cam in cameras:
        cid = str(cam.get("_id") or "")
        if not cid:
            continue
        seen.add(cid)
        try:
            await refresh_camera_config(cid, cam.get("motion_recording"))
            active = await _probe_camera_activity(cam)
            if active is True:
                await notify_motion_activity(cid, active=True, source="isapi_poll")
                # RDSO 18.1.22 — rising-edge motion → existing alarm evaluator
                await emit_motion_alarm_if_rising(cam, True, source="isapi_poll")
            elif active is False:
                await emit_motion_alarm_if_rising(cam, False, source="isapi_poll")
            await tick_idle_hold(cid)
        except Exception as exc:
            logger.debug("[MOTION] poll camera=%s: %s", cid, exc)

    # Digital-input / relay: only cameras with enabled digital_input rules
    try:
        di_cams = await list_cameras_with_source_rules("digital_input")
        for cam in di_cams:
            cid = str(cam.get("_id") or "")
            if not cid:
                continue
            try:
                cap = await probe_digital_input_capability(cam)
                if not cap.get("supported"):
                    continue
                active = await probe_digital_input_active(cam, port=1)
                if active is None:
                    continue
                await emit_digital_input_alarm_if_rising(
                    cam, active, source="isapi_io_poll", port=1
                )
            except Exception as exc:
                logger.debug("[CAMERA-EVENT] digital_input poll camera=%s: %s", cid, exc)
    except Exception as exc:
        logger.debug("[CAMERA-EVENT] digital_input rule scan: %s", exc)


async def _poll_loop() -> None:
    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[MOTION] poll loop error: %s", exc)
        await asyncio.sleep(DEFAULT_POLL_SECONDS)


def start_motion_recording_poller() -> asyncio.Task:
    global _poll_task
    if _poll_task and not _poll_task.done():
        return _poll_task
    _poll_task = asyncio.create_task(_poll_loop())
    logger.info("[MOTION] Activity poller started (hold expiry + optional ISAPI)")
    return _poll_task


async def stop_motion_recording_poller() -> None:
    global _poll_task
    if _poll_task and not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = None
