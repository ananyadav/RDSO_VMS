#!/usr/bin/env python3
"""RDSO 18.1.30 — software interoperability / integration package acceptance (logical).

Does not claim ONVIF Client List certification or multi-vendor lab results.
Reuses 18.3.15 Profile S/G software stack.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "sdk" / "integration"))


def main() -> int:
    print("RDSO 18.1.30 software acceptance (no external certification claim)")

    # Package artifacts
    for rel in (
        "docs/integration-api.md",
        "docs/integration-openapi.json",
        "docs/integration-package/README.md",
        "sdk/integration/vms_client.py",
    ):
        path = ROOT / rel
        assert path.is_file(), f"missing {rel}"
        print(f"[OK] {rel}")

    schema = json.loads((ROOT / "docs" / "integration-openapi.json").read_text(encoding="utf-8"))
    assert schema["info"]["version"] == "18.1.30"
    assert "/api/system/interop" in schema["paths"]
    print("[OK] OpenAPI version 18.1.30 + /api/system/interop")

    from vms_client import VmsClient, assert_no_camera_secrets

    assert hasattr(VmsClient, "onvif_profile_s")
    assert hasattr(VmsClient, "system_interop")
    assert_no_camera_secrets({"password": "***"})
    print("[OK] reference client + secret helper")

    from app.services.onvif_interop import integration_surface_public
    from app.services.onvif_stream_uri import should_resolve_onvif_for_recording
    from app.services.edge_onvif_recording import parse_replay_uri_response

    surface = integration_surface_public()
    assert surface["docs_on_disk"] and surface["reference_client_on_disk"]
    assert should_resolve_onvif_for_recording({"protocol": "ONVIF"})
    assert not should_resolve_onvif_for_recording({"protocol": "HIKVISION"})
    assert parse_replay_uri_response(
        "<GetReplayUriResponse><Uri>rtsp://x/r</Uri></GetReplayUriResponse>"
    ).startswith("rtsp://")
    print("[OK] Profile S policy + Profile G replay parse (18.3.15 reuse)")

    print("PASS (software). External ONVIF/client-list certification still pending if required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
