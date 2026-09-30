#!/usr/bin/env python3
"""RDSO 18.5(i) — remote web capacity acceptance (safe by default).

Default: software evidence only (no production load).
Opt-in 100 concurrent login soak against a *local/test* API only:

  set RDSO_18_5_I_ACCEPTANCE=1
  python backend/scripts/rdso_18_5_i_accept_remote_capacity.py --login-soak 100 \\
      --base-url http://127.0.0.1:10000 --username ... --password ...

Do NOT point --base-url at production.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _software_probe() -> int:
    from app.services.remote_web_capacity import get_rdso_18_5_i_capacity

    # Sync wrapper for async capacity without counts if event loop unavailable
    async def _run():
        return await get_rdso_18_5_i_capacity(include_counts=False)

    cap = asyncio.run(_run())
    print(json.dumps(cap, indent=2))
    if not cap.get("software_compliant"):
        print("FAIL: software capacity below RDSO 18.5(i) minima", file=sys.stderr)
        return 1
    print("[OK] Software capacity evidence meets 18.5(i) (>=1000 users / >=100 concurrent logins; no hard caps)")
    print("NOTE: Linux/WAN soak with >=1000 provisioned users still required for site acceptance.")
    return 0


async def _login_soak(base_url: str, username: str, password: str, count: int) -> int:
    import aiohttp

    url = base_url.rstrip("/") + "/api/login"
    success = 0
    errors: list[str] = []

    async with aiohttp.ClientSession() as session:
        async def one(i: int) -> None:
            nonlocal success
            try:
                async with session.post(
                    url,
                    json={"username": username, "password": password},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        success += 1
                    else:
                        body = await resp.text()
                        errors.append(f"#{i} status={resp.status} {body[:120]}")
            except Exception as exc:
                errors.append(f"#{i} {exc}")

        await asyncio.gather(*(one(i) for i in range(count)))

    print(json.dumps({"requested": count, "success": success, "errors": errors[:10]}, indent=2))
    if success < count:
        print(f"FAIL: only {success}/{count} concurrent logins succeeded", file=sys.stderr)
        return 1
    print(f"[OK] {success} concurrent logical logins succeeded")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RDSO 18.5(i) remote web capacity probe")
    parser.add_argument("--login-soak", type=int, default=0, help="Opt-in concurrent POST /api/login count")
    parser.add_argument("--base-url", default="http://127.0.0.1:10000")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    rc = _software_probe()
    if rc != 0:
        return rc

    if args.login_soak <= 0:
        return 0

    if os.getenv("RDSO_18_5_I_ACCEPTANCE", "").strip() not in ("1", "true", "yes"):
        print(
            "REFUSED: set RDSO_18_5_I_ACCEPTANCE=1 to run login soak "
            "(local/test API only — not production).",
            file=sys.stderr,
        )
        return 2

    base = (args.base_url or "").lower()
    if any(x in base for x in ("production", "prod.", "railway", "vercel")):
        print("REFUSED: base-url looks like production", file=sys.stderr)
        return 2
    if not args.username or not args.password:
        print("REFUSED: --username and --password required for login soak", file=sys.stderr)
        return 2

    return asyncio.run(_login_soak(args.base_url, args.username, args.password, args.login_soak))


if __name__ == "__main__":
    raise SystemExit(main())
