"""Honest external VMS adapter errors — no fake success (RDSO 18.6.17.3)."""

from __future__ import annotations


class VmsAdapterError(Exception):
    """Base adapter failure."""

    code = "adapter_error"

    def __init__(self, message: str, *, code: str | None = None, detail: str | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.detail = detail or ""


class UnsupportedCapabilityError(VmsAdapterError):
    code = "unsupported_capability"


class VmsSourceOfflineError(VmsAdapterError):
    code = "source_offline"


class VmsAuthFailureError(VmsAdapterError):
    code = "authentication_failure"


class VmsTimeoutError(VmsAdapterError):
    code = "timeout"


class VmsDirectCameraForbiddenError(VmsAdapterError):
    code = "direct_camera_forbidden"
