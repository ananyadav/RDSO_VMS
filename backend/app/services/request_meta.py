"""Client IP / user-agent helpers for audit and sessions."""

from __future__ import annotations

from aiohttp import web


def client_ip(request: web.Request) -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    forwarded = (request.headers.get("X-Real-IP") or "").strip()
    if forwarded:
        return forwarded
    return (request.remote or "").strip()


def user_agent(request: web.Request) -> str:
    return (request.headers.get("User-Agent") or "")[:512]
