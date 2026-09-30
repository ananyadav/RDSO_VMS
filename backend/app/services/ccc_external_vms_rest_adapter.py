"""Generic REST third-party VMS adapter — no named vendor SDK claims (18.6.17.3)."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.services.ccc_vms_adapter_errors import (
    UnsupportedCapabilityError,
    VmsAdapterError,
    VmsAuthFailureError,
    VmsDirectCameraForbiddenError,
    VmsSourceOfflineError,
    VmsTimeoutError,
)
from app.services.ccc_vms_integration_store import touch_integration_health
from app.services.ccc_vms_source import ccc_public_camera, sanitize_ccc_media_payload

logger = logging.getLogger(__name__)


def _external_camera_id(camera_ref: str, source_id: str) -> str:
    prefix = f"{source_id}:"
    ref = str(camera_ref or "").strip()
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref


def _namespaced_id(source_id: str, external_id: str) -> str:
    return f"{source_id}:{external_id}"


class GenericRestVmsSource:
    """Purchaser-provided REST VMS — CCC → adapter → external VMS (never direct camera)."""

    def __init__(self, config: dict[str, Any]):
        self.source_id = str(config["source_id"])
        self.label = str(config.get("label") or self.source_id)
        self._config = config

    def describe(self) -> dict[str, Any]:
        caps = dict(self._config.get("capabilities") or {})
        return {
            "source_id": self.source_id,
            "label": self.label,
            "kind": "external_vms",
            "vendor_type": self._config.get("vendor_type") or "generic_rest",
            "adapter_kind": "generic_rest",
            "browser_only": True,
            "requires_separate_client": False,
            "direct_camera_rtsp": False,
            "external_vendor_integration": True,
            "named_vendor_sdk": False,
            "fake_vendor": False,
            "pluggable": True,
            "capabilities": caps,
            "base_url": self._config.get("base_url") or "",
            "video_path": "CCC → VMS adapter → external VMS API",
            "note": (
                "Generic REST adapter only — no proprietary vendor SDK is bundled. "
                "Purchaser maps REST endpoints to this deployment."
            ),
        }

    def _caps(self) -> dict[str, bool]:
        return dict(self._config.get("capabilities") or {})

    def _require_cap(self, name: str) -> None:
        if not self._caps().get(name):
            raise UnsupportedCapabilityError(
                f"Capability '{name}' not enabled for source {self.source_id}"
            )

    def _auth_headers(self) -> dict[str, str]:
        cred = str(self._config.get("credential") or "").strip()
        auth_type = str(self._config.get("auth_type") or "bearer").lower()
        header = str(self._config.get("auth_header") or "Authorization")
        if not cred:
            return {}
        if auth_type == "bearer":
            return {header: f"Bearer {cred}"}
        if auth_type == "api_key_header":
            return {header: cred}
        if auth_type == "basic":
            import base64

            token = base64.b64encode(cred.encode("utf-8")).decode("ascii")
            return {header: f"Basic {token}"}
        return {header: cred}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        base = str(self._config.get("base_url") or "").rstrip("/")
        url = urljoin(base + "/", path.lstrip("/"))
        timeout = aiohttp.ClientTimeout(
            total=max(3, min(int(self._config.get("timeout_seconds") or 10), 60))
        )
        verify = bool(self._config.get("tls_verify", True))
        headers = {"Accept": "application/json", **self._auth_headers()}
        try:
            connector = aiohttp.TCPConnector(ssl=verify)
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.request(
                    method, url, params=params, json=json_body, headers=headers
                ) as resp:
                    text = await resp.text()
                    if resp.status in (401, 403):
                        await touch_integration_health(
                            self.source_id,
                            health="offline",
                            error=f"auth HTTP {resp.status}",
                        )
                        raise VmsAuthFailureError(
                            f"External VMS authentication failed (HTTP {resp.status})"
                        )
                    if resp.status >= 500:
                        await touch_integration_health(
                            self.source_id,
                            health="offline",
                            error=f"HTTP {resp.status}",
                        )
                        raise VmsSourceOfflineError(
                            f"External VMS unavailable (HTTP {resp.status})"
                        )
                    if resp.status >= 400:
                        raise VmsAdapterError(
                            f"External VMS error HTTP {resp.status}: {text[:200]}"
                        )
                    if not text:
                        await touch_integration_health(
                            self.source_id, health="healthy", success=True
                        )
                        return {}
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise VmsAdapterError("External VMS returned non-JSON") from exc
                    await touch_integration_health(
                        self.source_id, health="healthy", success=True
                    )
                    return data
        except aiohttp.ClientError as exc:
            await touch_integration_health(
                self.source_id, health="offline", error=str(exc)[:200]
            )
            if "timeout" in str(exc).lower():
                raise VmsTimeoutError(f"External VMS timeout: {exc}") from exc
            raise VmsSourceOfflineError(f"External VMS unreachable: {exc}") from exc

    def _normalize_camera_item(self, item: dict) -> dict[str, Any]:
        ext_id = str(item.get("id") or item.get("camera_id") or item.get("uid") or "")
        doc = {
            "_id": _namespaced_id(self.source_id, ext_id),
            "id": _namespaced_id(self.source_id, ext_id),
            "name": item.get("name") or ext_id,
            "display_name": item.get("display_name") or item.get("displayName") or item.get("name") or ext_id,
            "camera_uid": str(item.get("camera_uid") or item.get("uid") or ext_id),
            "online": bool(item.get("online", item.get("is_online", False))),
            "ptz": bool(item.get("ptz") or item.get("is_ptz")),
            "location_path": item.get("location_path") or item.get("location") or "",
            "camera_group": item.get("camera_group") or "",
            "worker_id": item.get("worker_id") or item.get("workerId") or 1,
            "is_active": bool(item.get("is_active", True)),
            "password": None,
            "main_rtsp_url": None,
            "sub_rtsp_url": None,
        }
        pub = ccc_public_camera(doc)
        pub["source_id"] = self.source_id
        pub["external_camera_id"] = ext_id
        pub["external_vms"] = True
        return pub

    async def list_cameras_public(
        self,
        user: dict[str, Any],
        *,
        limit: int = 100,
        offset: int = 0,
        q: str = "",
    ) -> dict[str, Any]:
        self._require_cap("live")
        endpoints = self._config.get("endpoints") or {}
        path = str(endpoints.get("cameras") or "/api/cameras")
        params = {"limit": limit, "offset": offset}
        if q:
            params["q"] = q
        data = await self._request("GET", path, params=params)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            raw_items = []
        items = [self._normalize_camera_item(x) for x in raw_items if isinstance(x, dict)]
        total = int(data.get("total") if isinstance(data, dict) else len(items))
        return {
            "source_id": self.source_id,
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "returned": len(items),
            "streams_not_auto_started": True,
            "external_vms": True,
            "rdso_18_6_17_3": True,
        }

    async def client_media_for(self, camera_ref: str, user: dict[str, Any]) -> dict[str, Any]:
        self._require_cap("live")
        endpoints = self._config.get("endpoints") or {}
        template = str(endpoints.get("live_media") or "/api/cameras/{camera_id}/client-media")
        ext_id = _external_camera_id(camera_ref, self.source_id)
        path = template.replace("{camera_id}", ext_id).replace("{id}", ext_id)
        data = await self._request("GET", path)
        if not isinstance(data, dict):
            raise VmsAdapterError("Invalid client-media response")
        blob = json.dumps(data)
        if "rtsp://" in blob.lower():
            raise VmsDirectCameraForbiddenError(
                "External VMS returned direct camera RTSP — forbidden for CCC"
            )
        cleaned = sanitize_ccc_media_payload(data)
        cleaned["source_id"] = self.source_id
        cleaned["external_vms"] = True
        cleaned["via_adapter"] = True
        return cleaned

    async def search_playback_public(
        self,
        user: dict[str, Any],
        camera_ref: str,
        *,
        from_ts: str = "",
        to_ts: str = "",
    ) -> dict[str, Any]:
        self._require_cap("playback")
        endpoints = self._config.get("endpoints") or {}
        path = str(endpoints.get("playback_search") or "/api/playback/search")
        ext_id = _external_camera_id(camera_ref, self.source_id)
        params: dict[str, str] = {"camera_id": ext_id}
        if from_ts:
            params["from"] = from_ts
        if to_ts:
            params["to"] = to_ts
        data = await self._request("GET", path, params=params)
        return {
            "source_id": self.source_id,
            "camera_ref": camera_ref,
            "external_camera_id": ext_id,
            "supported": True,
            "items": (data.get("items") if isinstance(data, dict) else []) or [],
            "via_adapter": True,
            "direct_camera_rtsp": False,
        }

    async def health_status(self) -> dict[str, Any]:
        caps = self._caps()
        if not caps.get("health"):
            return {
                "source_id": self.source_id,
                "status": "unknown",
                "message": "health capability disabled",
            }
        endpoints = self._config.get("endpoints") or {}
        path = str(endpoints.get("health") or "/api/health")
        try:
            data = await self._request("GET", path)
            status = "healthy"
            if isinstance(data, dict) and data.get("ok") is False:
                status = "degraded"
            return {
                "source_id": self.source_id,
                "status": status,
                "external": data if isinstance(data, dict) else {},
                "via_adapter": True,
            }
        except VmsAdapterError as exc:
            return {
                "source_id": self.source_id,
                "status": "offline",
                "error": str(exc),
                "code": exc.code,
            }

    async def test_connection(self) -> dict[str, Any]:
        result = await self.health_status()
        ok = result.get("status") == "healthy"
        return {"ok": ok, **result}
