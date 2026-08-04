"""Shared HTTP helpers for aiohttp routes."""

from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from aiohttp import web


async def read_json_body(request: web.Request) -> Tuple[Optional[dict], Optional[web.Response]]:
    """Parse JSON request body; return (data, error_response)."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return None, web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(data, dict):
        return None, web.json_response({"error": "JSON object required"}, status=400)
    return data, None


@web.middleware
async def json_error_middleware(request: web.Request, handler):
    """Return 400 instead of 500 when a handler receives malformed JSON."""
    try:
        return await handler(request)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
