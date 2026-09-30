"""Camera event inputs → existing alarm evaluator (RDSO 18.1.22).

Rising-edge adapters for motion and digital-input / relay-input signals.
Reuses process_alarm_signal — does not create a second alarm engine.
Unsupported hardware is reported honestly (no fake events).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# (camera_id, source_type) → last observed active bool
_PREV_ACTIVE: dict[tuple[str, str], bool] = {}

MOTION_TITLE = "Motion detected"
DIGITAL_INPUT_TITLE = "Digital input / relay event"


def reset_camera_event_input_state_for_tests() -> None:
    _PREV_ACTIVE.clear()


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def is_rising_edge(previous: bool | None, current: bool) -> bool:
    """Emit only inactive/unknown → active (storm prevention while held)."""
    if not current:
        return False
    if previous is True:
        return False
    return True


def note_activity_state(camera_id: str, source_type: str, active: bool) -> bool | None:
    """Update remembered state; return previous value (or None)."""
    key = (str(camera_id), str(source_type))
    prev = _PREV_ACTIVE.get(key)
    _PREV_ACTIVE[key] = bool(active)
    return prev


def build_camera_event_signal(
    camera: dict,
    *,
    source_type: str,
    title: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cid = str(camera.get("_id") or camera.get("id") or "")
    return {
        "camera_id": cid,
        "camera_uid": str(camera.get("camera_uid") or ""),
        "source_type": source_type,
        "title": title,
        "message": message,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "active": True,
            **(metadata or {}),
        },
    }


async def emit_camera_event_on_rising_edge(
    camera: dict,
    *,
    source_type: str,
    active: bool,
    title: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """If rising edge, call existing process_alarm_signal; else skip (no storm)."""
    cid = str(camera.get("_id") or camera.get("id") or "")
    prev = note_activity_state(cid, source_type, active)
    if not is_rising_edge(prev, active):
        return {
            "emitted": False,
            "reason": "not_rising_edge" if active else "inactive",
            "previous": prev,
            "active": active,
        }

    from app.services.alarm_rule_evaluator import process_alarm_signal

    signal = build_camera_event_signal(
        camera,
        source_type=source_type,
        title=title,
        message=message,
        metadata=metadata,
    )
    try:
        result = await process_alarm_signal(signal)
    except Exception as exc:
        logger.warning("[CAMERA-EVENT] evaluator failed camera=%s type=%s: %s", cid, source_type, exc)
        return {"emitted": False, "reason": "evaluator_error", "error": str(exc)}
    return {"emitted": True, "previous": prev, "active": active, "result": result}


def parse_io_input_port_nums(text: str) -> Optional[int]:
    """Parse IOInputPortNums from ISAPI IO capabilities / list."""
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        lower = text.lower()
        if "ioinputportnums>0<" in lower.replace(" ", ""):
            return 0
        return None
    for node in root.iter():
        if _local(node.tag).lower() in ("ioinputportnums", "inputportnums"):
            val = (node.text or "").strip()
            if val.isdigit():
                return int(val)
    # Empty IOInputPortList ⇒ 0 ports
    if "IOInputPortList" in text and "<IOInputPort" not in text.replace("IOInputPortList", ""):
        return 0
    return None


def parse_io_input_active(text: str) -> bool | None:
    """Parse a single IO input status XML — True/False/None."""
    if not text:
        return None
    lower = text.lower()
    if any(x in lower for x in ("<ioistate>active", "state>active", "<status>active")):
        return True
    if any(x in lower for x in ("<ioistate>inactive", "state>inactive", "<status>inactive")):
        return False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for node in root.iter():
        tag = _local(node.tag).lower()
        val = (node.text or "").strip().lower()
        if tag in ("ioistate", "state", "status", "triggered") and val:
            if val in ("active", "true", "1", "high", "on"):
                return True
            if val in ("inactive", "false", "0", "low", "off"):
                return False
    return None


async def probe_digital_input_capability(camera: dict) -> dict[str, Any]:
    """Honest capability probe — supported only when device exposes IO inputs."""
    from app.services.hikvision_ptz import _isapi
    from app.services.motion_capability import _prefer_isapi

    result: dict[str, Any] = {
        "supported": False,
        "protocol": None,
        "input_ports": 0,
        "message": "Digital input / relay not available on this camera",
    }
    if not _prefer_isapi(camera):
        result["message"] = "Digital input probe uses Hikvision ISAPI; camera protocol not ISAPI-preferring"
        return result

    try:
        status, text = await _isapi(camera, "GET", "/ISAPI/System/IO/capabilities")
    except Exception as exc:
        result["message"] = f"IO capabilities unreachable: {exc}"
        return result

    if status != 200:
        result["message"] = f"IO capabilities not available (HTTP {status})"
        return result

    ports = parse_io_input_port_nums(text or "")
    if ports is None:
        # Try list endpoint
        try:
            st2, text2 = await _isapi(camera, "GET", "/ISAPI/System/IO/inputs")
            if st2 == 200:
                if "<IOInputPort" in (text2 or "") and "IOInputPortList" in (text2 or ""):
                    ports = (text2 or "").count("<IOInputPort")
                else:
                    ports = parse_io_input_port_nums(text2 or "") or 0
        except Exception:
            ports = 0

    if not ports:
        result["message"] = "Camera reports zero digital inputs (relay/IO unsupported)"
        result["input_ports"] = 0
        return result

    result.update(
        {
            "supported": True,
            "protocol": "hikvision_isapi",
            "input_ports": int(ports),
            "message": f"ISAPI digital inputs available ({ports})",
        }
    )
    return result


async def probe_digital_input_active(camera: dict, *, port: int = 1) -> bool | None:
    """Read one IO input status. None = unknown / unsupported (do not fake)."""
    from app.services.hikvision_ptz import _isapi

    cap = await probe_digital_input_capability(camera)
    if not cap.get("supported"):
        return None
    path = f"/ISAPI/System/IO/inputs/{int(port)}/status"
    try:
        status, text = await _isapi(camera, "GET", path)
    except Exception:
        return None
    if status != 200:
        return None
    return parse_io_input_active(text or "")


async def emit_motion_alarm_if_rising(camera: dict, active: bool, *, source: str) -> dict[str, Any]:
    return await emit_camera_event_on_rising_edge(
        camera,
        source_type="motion",
        active=active,
        title=MOTION_TITLE,
        message="Camera motion/activity detected",
        metadata={"source": source},
    )


async def emit_digital_input_alarm_if_rising(
    camera: dict,
    active: bool,
    *,
    source: str,
    port: int = 1,
) -> dict[str, Any]:
    return await emit_camera_event_on_rising_edge(
        camera,
        source_type="digital_input",
        active=active,
        title=DIGITAL_INPUT_TITLE,
        message=f"Digital input / relay port {port} active",
        metadata={"source": source, "port": port, "relay": True},
    )


async def list_cameras_with_source_rules(source_type: str) -> list[dict]:
    """Cameras that have an enabled alarm rule for this source type."""
    from app.core.database import database
    from bson import ObjectId

    rules = database.get_collection("alarm_rules")
    cameras = database.get_collection("cameras")
    camera_ids: set[str] = set()
    async for rule in rules.find(
        {
            "enabled": True,
            "trigger.source_type": source_type,
        },
        {"camera_id": 1},
    ):
        cid = str(rule.get("camera_id") or "").strip()
        if cid:
            camera_ids.add(cid)
    out = []
    for cid in camera_ids:
        if not ObjectId.is_valid(cid):
            continue
        doc = await cameras.find_one({"_id": ObjectId(cid)})
        if doc:
            out.append(doc)
    return out
