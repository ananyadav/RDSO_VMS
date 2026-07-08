"""Minimal HTTP Digest authentication helper for aiohttp."""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp


def _parse_digest_challenge(header: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not header.lower().startswith("digest"):
        return out
    for match in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', header):
        key = match[0]
        val = match[1] if match[1] else match[2]
        out[key] = val
    return out


def _build_digest_header(
    *,
    method: str,
    url: str,
    username: str,
    password: str,
    challenge: Dict[str, str],
) -> str:
    parsed = urlparse(url)
    uri = parsed.path or "/"
    if parsed.query:
        uri = f"{uri}?{parsed.query}"

    realm = challenge.get("realm", "")
    nonce = challenge.get("nonce", "")
    qop = challenge.get("qop", "")
    if qop and "," in qop:
        qop = qop.split(",")[0].strip()
    opaque = challenge.get("opaque", "")

    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()

    if qop:
        nc = "00000001"
        cnonce = secrets.token_hex(8)
        response = hashlib.md5(
            f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()
        ).hexdigest()
        header = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", qop={qop}, nc={nc}, cnonce="{cnonce}", response="{response}"'
        )
    else:
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        header = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{response}"'
        )

    if opaque:
        header += f', opaque="{opaque}"'
    if challenge.get("algorithm"):
        header += f', algorithm={challenge["algorithm"]}'
    return header


async def request_with_digest(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    username: str,
    password: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: aiohttp.ClientTimeout,
) -> Tuple[int, str]:
    """Return (status_code, response_text) using Digest or Basic auth."""
    req_headers = dict(headers or {})

    async with session.request(
        method,
        url,
        data=data,
        headers=req_headers,
        timeout=timeout,
        allow_redirects=False,
        ssl=False,
    ) as resp:
        if resp.status != 401:
            return resp.status, await resp.text(errors="replace")
        auth_header = resp.headers.get("WWW-Authenticate", "")

    if auth_header.lower().startswith("digest"):
        challenge = _parse_digest_challenge(auth_header)
        req_headers["Authorization"] = _build_digest_header(
            method=method,
            url=url,
            username=username,
            password=password,
            challenge=challenge,
        )
    else:
        req_headers["Authorization"] = aiohttp.BasicAuth(username, password).encode()

    async with session.request(
        method,
        url,
        data=data,
        headers=req_headers,
        timeout=timeout,
        allow_redirects=False,
        ssl=False,
    ) as resp:
        return resp.status, await resp.text(errors="replace")
