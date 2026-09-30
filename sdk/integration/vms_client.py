"""Thin reference Integration Client for RDSO 18.1.30 / 18.3.15.

This is **not** a proprietary binary SDK. It wraps the authenticated REST API
documented in docs/integration-api.md and docs/integration-openapi.json.

Example:
    from vms_client import VmsClient
    client = VmsClient("http://127.0.0.1:8080")
    client.login("admin", "secret")
    print(client.system_interop())
    print(client.cameras())
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Optional


class VmsClientError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class VmsClient:
    """Session-cookie REST helper for purchaser integrators."""

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))

    def _url(self, path: str, query: Optional[dict] = None) -> str:
        path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict] = None,
        body: Any = None,
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path, query), data=data, headers=headers, method=method.upper())
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "json" in ctype:
                    return json.loads(raw.decode("utf-8"))
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            payload: Any
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = None
            raise VmsClientError(
                f"{method.upper()} {path} failed: HTTP {exc.code}",
                status=exc.code,
                body=payload,
            ) from exc

    def login(self, username: str, password: str) -> dict:
        return self.request("POST", "/api/login", body={"username": username, "password": password})

    def logout(self) -> Any:
        return self.request("POST", "/api/logout", body={})

    def session(self) -> dict:
        return self.request("GET", "/api/auth/session")

    def cameras(self) -> Any:
        return self.request("GET", "/api/cameras")

    def configured_cameras(self) -> Any:
        return self.request("GET", "/api/cameras/configured")

    def client_media(self, camera_id: str) -> Any:
        return self.request("GET", f"/api/cameras/{camera_id}/client-media")

    def recording_status(self, camera_id: str) -> Any:
        return self.request("GET", f"/api/recordings/{camera_id}/status")

    def playback_search(self, **query: Any) -> Any:
        return self.request("GET", "/api/playback/search", query=query)

    def events(self, **query: Any) -> Any:
        return self.request("GET", "/api/events", query=query)

    def export_playback(self, payload: dict) -> Any:
        return self.request("POST", "/api/playback/export", body=payload)

    def onvif_profile_s(self, camera_id: str) -> Any:
        return self.request("GET", f"/api/cameras/{camera_id}/onvif/profile-s")

    def resolve_onvif_streams(self, camera_id: str, *, persist: bool = False) -> Any:
        return self.request(
            "POST",
            f"/api/cameras/{camera_id}/onvif/resolve-streams",
            body={"persist": bool(persist)},
        )

    def camera_interop(self, camera_id: str, *, resolve: bool = False) -> Any:
        return self.request(
            "GET",
            f"/api/cameras/{camera_id}/interop",
            query={"resolve": "1" if resolve else "0"},
        )

    def system_interop(self, *, limit: int = 200) -> Any:
        return self.request("GET", "/api/system/interop", query={"limit": int(limit)})

    def ccc_capability(self) -> Any:
        return self.request("GET", "/api/ccc/capability")

    def ccc_cameras(self, **query: Any) -> Any:
        return self.request("GET", "/api/ccc/cameras", query=query)

    def ccc_client_media(self, camera_id: str, **query: Any) -> Any:
        return self.request("GET", f"/api/ccc/cameras/{camera_id}/client-media", query=query)

    def ccc_status(self) -> Any:
        return self.request("GET", "/api/ccc/status")

    def edge_capability(self, camera_id: str) -> Any:
        return self.request("GET", f"/api/recordings/edge/capability/{camera_id}")

    def integration_docs(self) -> str:
        return self.request("GET", "/api/docs/integration")

    def integration_openapi(self) -> Any:
        return self.request("GET", "/api/docs/integration/openapi.json")


def assert_no_camera_secrets(payload: Any) -> None:
    """Helper for integrators/tests — fail if obvious secrets leak into JSON."""
    blob = json.dumps(payload, default=str)
    lowered = blob.lower()
    if '"password": "' in lowered and '"password": "***"' not in lowered and '"password":""' not in lowered:
        # Allow masked form only
        if '"password": "***"' not in blob and "password" in lowered:
            # Heuristic: reject non-masked password fields with length > 3
            if '"password": "***"' not in blob:
                import re

                for match in re.finditer(r'"password"\s*:\s*"([^"]*)"', blob, flags=re.I):
                    val = match.group(1)
                    if val and val != "***":
                        raise AssertionError("Camera password must not appear in API responses")
    if "rtsp://" in lowered and "@" in lowered:
        # Credentialed RTSP must not appear
        if "rtsp://" in blob and "@" in blob:
            import re

            if re.search(r"rtsp://[^/@\s]+:[^/@\s]+@", blob, flags=re.I):
                raise AssertionError("RTSP credentials must be masked in API responses")
