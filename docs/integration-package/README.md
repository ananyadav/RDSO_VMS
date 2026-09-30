# VMS Integration Package (RDSO 18.1.30 / 18.3.15)

Purchaser-facing integration package for the CCTV VMS.

## What this is

Authenticated **REST** is the supported integration interface (Clause 18.1.30 / 18.3.15).
There is **no proprietary binary SDK**. A thin Python reference client is included for convenience.

## Package contents

| Artifact | Location |
|----------|----------|
| Integration guide | [`../integration-api.md`](../integration-api.md) |
| OpenAPI 3 schema | [`../integration-openapi.json`](../integration-openapi.json) |
| Reference client | [`../../sdk/integration/vms_client.py`](../../sdk/integration/vms_client.py) |
| Live docs (running VMS) | `GET /api/docs/integration` |
| Live OpenAPI | `GET /api/docs/integration/openapi.json` |
| Fleet interop status | `GET /api/system/interop` (admin) |
| Per-camera interop | `GET /api/cameras/{id}/interop` (admin + camera ACL) |

## Covered surfaces

1. **Authentication** — `POST /api/login` → session cookie; `GET /api/auth/session`
2. **Cameras** — list / configured / CRUD (passwords always masked)
3. **Live / media routing** — `GET /api/cameras/{id}/client-media` (server-independent paths)
4. **Recording / status** — `/api/recordings/...`
5. **Playback / search / export** — `/api/playback/...`
6. **Events / alarms** — `/api/events`, `/api/alarm-rules`
7. **ONVIF Profile S** — GetProfiles / GetStreamUri via `/onvif/profile-s` and `/onvif/resolve-streams`
8. **ONVIF Profile G / edge** — `/api/recordings/edge/...` (capability, search/backfill)

## Quick start (reference client)

```python
import sys
sys.path.insert(0, "sdk/integration")
from vms_client import VmsClient, assert_no_camera_secrets

client = VmsClient("http://127.0.0.1:8080")
client.login("admin", "your-password")
status = client.system_interop()
cams = client.configured_cameras()
assert_no_camera_secrets(cams)
```

## Security

- Camera passwords are never returned (masked as `***`)
- RTSP URLs in API responses have credentials stripped/masked
- Role-based access + camera ACL remain enforced on camera-scoped routes

## Certification note

Software interoperability (ONVIF Profile S/G operations + REST integration) is implemented.
**Accredited ONVIF Client List / third-party interoperability certification** is a separate
supply-acceptance item and is **not** claimed by this package alone.
