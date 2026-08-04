"""Recorder agent contracts — separate process from aiohttp API (not implemented yet)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class RecordingLeaseState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class RecordingLease:
    """Exclusive right for one agent to record one camera."""

    lease_id: str
    camera_id: str
    camera_uid: str
    agent_id: str
    session_id: str
    state: RecordingLeaseState = RecordingLeaseState.PENDING
    acquired_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    storage_path: str = ""
    rtsp_url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentHeartbeat:
    agent_id: str
    hostname: str
    pid: int
    active_leases: int
    reported_at: datetime
    version: str = "0.0.0"


@dataclass
class FfmpegProcessStatus:
    lease_id: str
    camera_id: str
    pid: Optional[int]
    running: bool
    last_segment_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    stderr_tail: str = ""


@runtime_checkable
class RecordingLeaseStore(Protocol):
    """MongoDB (or Redis) backing store for leases — implemented by future agent coordinator."""

    async def try_acquire_lease(
        self,
        *,
        camera_id: str,
        camera_uid: str,
        agent_id: str,
        session_id: str,
        ttl_seconds: int,
    ) -> Optional[RecordingLease]: ...

    async def renew_lease(self, lease_id: str, *, agent_id: str) -> bool: ...

    async def release_lease(self, lease_id: str, *, agent_id: str, reason: str) -> bool: ...

    async def list_stale_leases(self, *, older_than_seconds: int) -> list[RecordingLease]: ...


@runtime_checkable
class RecordingSessionStore(Protocol):
    """Updates MongoDB recording_sessions from the agent."""

    async def mark_session_started(self, session_id: str, *, agent_id: str, pid: int) -> None: ...

    async def mark_session_stopped(
        self,
        session_id: str,
        *,
        reason: str,
        exit_code: Optional[int] = None,
    ) -> None: ...

    async def touch_session_heartbeat(self, session_id: str) -> None: ...


@runtime_checkable
class FfmpegSupervisor(Protocol):
    """Starts/stops FFmpeg child processes for one lease."""

    async def start(self, lease: RecordingLease) -> FfmpegProcessStatus: ...

    async def stop(self, lease_id: str, *, graceful_seconds: int = 10) -> FfmpegProcessStatus: ...

    async def poll(self, lease_id: str) -> FfmpegProcessStatus: ...


@runtime_checkable
class RecorderAgent(Protocol):
    """Long-running worker: claim leases, supervise FFmpeg, publish heartbeats."""

    agent_id: str

    async def run_forever(self) -> None: ...

    async def shutdown(self) -> None: ...
