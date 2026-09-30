# Integration API (RDSO 18.1.30 / 18.3.15)

Authenticated REST is the supported integration surface. There is no proprietary binary SDK.
A thin reference Python client is provided at `sdk/integration/vms_client.py` (see `docs/integration-package/`).

**Clause mapping:** ONVIF Profile S/G software interoperability was implemented under **18.3.15**
(edge/Profile G paths also **18.3.16**) and is reused for **18.1.30**. This guide is the purchaser-facing
integration package.

## Authentication

- Session cookie after `POST /api/login`
- Legacy header `X-User-Id` (where enabled)
- Unauthenticated callers receive `401`

## Authorization

- Role-based access (SUPER_ADMIN / ADMIN / OPERATOR / VIEWER + permission flags)
- Camera ACL via `user_can_access_camera` on camera-scoped routes
- Camera passwords and RTSP credentials are **never** returned in API responses (masked as `***` / host-only URLs)

## Stable endpoints

### Cameras
| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/cameras` | Live lean camera list (ACL filtered) |
| GET | `/api/cameras/configured` | Management list (credentials masked) |
| GET | `/api/cameras/groups` | Location groups |
| POST/PUT/DELETE | `/api/cameras`, `/api/cameras/{id}` | Admin camera CRUD |
| GET | `/api/cameras/{id}/stream-profile` | Encoder profile |
| GET | `/api/cameras/{id}/client-media` | Server-independent live/media routing |
| GET | `/api/cameras/{id}/onvif/profile-s` | ONVIF Profile S profiles + stream URIs (masked) |
| POST | `/api/cameras/{id}/onvif/resolve-streams` | Resolve/persist GetStreamUri |
| GET | `/api/cameras/{id}/interop` | Profile S/G + vendor capability summary |

### System interoperability (18.1.30)
| Method | Path | Auth |
|--------|------|------|
| GET | `/api/system/interop` | admin |

Fleet/software status: Profile S/G availability, integration API pointers, vendor brands, certification disclaimer.

### Recording status / control
| Method | Path |
|--------|------|
| GET | `/api/recordings/{cameraId}/status` |
| GET | `/api/recordings/health` |
| GET | `/api/recordings/capability` |
| POST | `/api/recordings/{cameraId}/start` · `/stop` · `/toggle` |

### Playback / search / export
| Method | Path |
|--------|------|
| GET | `/api/playback/search` |
| GET | `/api/playback/multi-search` |
| GET | `/api/playback/dates` |
| POST | `/api/playback/export` |
| GET | `/api/recordings/sessions/{sessionId}/export` |

### Events / alarms
| Method | Path |
|--------|------|
| GET | `/api/events` |
| GET | `/api/events/{id}` |
| POST | `/api/events/{id}/acknowledge` |
| GET/POST | `/api/alarm-rules` |

### Edge storage (Profile G / vendor ISAPI) — 18.3.16
| Method | Path |
|--------|------|
| GET | `/api/recordings/edge/capability/{cameraId}` |
| GET | `/api/recordings/edge/gaps` |
| POST | `/api/recordings/edge/backfill` |
| GET | `/api/recordings/edge/jobs` |

Super-admin for edge mutation routes.

### Recording-server HA (N:1) — 18.3.3
| Method | Path |
|--------|------|
| GET | `/api/recordings/ha/status` |
| POST | `/api/recordings/ha/heartbeat` |
| POST | `/api/recordings/ha/servers` |
| POST | `/api/recordings/ha/assign` |
| POST | `/api/recordings/ha/failover` |
| POST | `/api/recordings/ha/failback` |

Enable with `RECORDING_HA_ENABLED=true` and distinct `RECORDING_SERVER_ID` / `RECORDING_SERVER_ROLE` per host. Default off (single process still stamps `recording_server_id` on sessions).

### System capacity (RDSO 18.1.27)
| Method | Path | Auth |
|--------|------|------|
| GET | `/api/system/capacity` | authenticated |
| GET | `/api/recordings/capability` | authenticated (includes `rdso_18_1_27` block) |

Software minima: ≥128 concurrent streams (no soft-cap below 128 by default; go2rtc workers distribute), ≥8 logical Live View display windows (`?monitor=1..8`), ≥5 numeric priority levels (distinct from RBAC), ≥16 simultaneous multi-camera replay. Physical 8-monitor + Linux 128-stream soak remain deployment acceptance. Opt-in: `python backend/scripts/rdso_18_1_27_accept_capacity.py` (does not open 128 streams).

### Network video transport (RDSO 18.2.2 / 18.2.3 / 18.2.27)
| Method | Path | Auth |
|--------|------|------|
| GET | `/api/system/transport` | authenticated |
| GET | `/api/cameras/{id}/transport` | camera ACL |
| PUT | `/api/cameras/{id}/transport` | admin + camera ACL |
| GET | `/api/cameras/{id}/client-media` | camera ACL (relative LAN/WAN paths) |

Default: unicast RTSP/TCP ingest + browser unicast via `/media/wN`. Optional per-camera multicast *source* ingest (`udp_mpegts` / `rtp` / `rtsp_multicast`); browsers still receive VMS-relayed unicast — no fake browser multicast claim. Multicast LAN acceptance is deployment/network testing.

### Live View display layouts (RDSO 18.2.5–18.2.7)
Advertised on `GET /api/go2rtc/live-config` → `live_display`:
- Full screen (1×1), Quad (2×2), 4×4 (16 tiles), plus 3×3 / 5×5 / 6×6 site divisions
- Grid tiles use sub-stream; fullscreen uses main-stream
- Software supports ≥16 simultaneous tiles and does **not** throttle FPS below 25
- Sustained 16×@25fps is workstation/network acceptance (not claimed by unit tests)

### Display / monitor control (RDSO 18.2.24–18.2.26)
Same `live_display` block also advertises:
- Resolution-responsive Live View (no hardcoded physical/LFD pixel size)
- Client-PC control: Open display… for logical monitors 1–8, layout select, per-slot camera assign
- Independent layout/camera state per monitor
- **Does not** claim a physical 55″ LFD was tested — place browser/fullscreen windows via OS multi-monitor for site acceptance

### Digital zoom (RDSO 18.2.21 / 18.2.22)
Fullscreen Live View (`FullscreenCameraModal`) provides **client-side** digital zoom (1×–8×) with pan while zoomed:
- Applies CSS transform to the existing go2rtc player — no second player
- Works for fixed and PTZ cameras; does **not** call PTZ/ONVIF move APIs
- Optical PTZ remains the separate `LivePtzPad`
- Resets on camera change / modal close; gated by Live View permission (ACL/RBAC unchanged)

### PTZ patterns (RDSO 18.2.23)
Patterns are **recorded pan/tilt/zoom paths**, distinct from tours/patrols (preset sequences):
| Method | Path |
|--------|------|
| GET | `/api/ptz/{id}/patterns` |
| PUT/DELETE | `/api/ptz/{id}/patterns/{patternId}` |
| POST | `…/start` · `…/stop` · `…/record-start` · `…/record-stop` |

- Hikvision ISAPI: full list/start/stop/record/delete when `/patterns` responds
- Dahua CGI: start/stop/record on slots 1–4 (no rename/delete inventory)
- ONVIF: honest **501 unsupported** (no standard Pattern; PresetTour ≠ Pattern)
- Audited: `PTZ_PATTERN_*` actions; same Live View + camera ACL as other PTZ

### Alarm-driven display switch (RDSO 18.2.28)
Alarm rules with `ui_notification` may include optional `display`:
```json
{
  "mode": "layout_switch",
  "monitor_id": 1,
  "layout": "2x2",
  "slot": 0,
  "restore_on_reset": true
}
```
Fired events carry `metadata.display_switch`. Matching Live View monitors apply layout/slot via existing go2rtc tiles; without config, fullscreen auto-display (18.1.25) is unchanged. Reset/recovery can restore the prior layout/slots; acknowledge stays separate.

### Video title + date/time overlay (RDSO 18.2.29)
Client-side GUI overlay on Live View tiles and fullscreen (`VideoTitleTimeOverlay`):
- Camera title + continuous date/time in **APP_TIMEZONE**
- Not burned into the stream; `pointer-events: none` so PTZ / digital zoom stay usable
- Hardware camera OSD (if any) is separate and unchanged

### Remote web capacity (RDSO 18.5(i))
- Mongo `users` + opaque `sessions` cookies — **no hard cap** below 1000 users / 100 concurrent logins by default
- Evidence: `GET /api/system/remote-web-capacity`
- Opt-in local login soak only: `RDSO_18_5_I_ACCEPTANCE=1` + `backend/scripts/rdso_18_5_i_accept_remote_capacity.py --login-soak 100` (never production)

### Remote bandwidth transcoding (RDSO 18.5(ii))
On-demand FFmpeg HLS for remote clients (`POST /api/remote-transcode/sessions`):
- **Live** input = go2rtc local RTSP (never camera credentials)
- **Playback** input = existing recording session media
- `mode=auto` + `bandwidth_kbps` selects FPS / resolution / H.264 bitrate profile
- `mode=direct` or high Auto bandwidth → unchanged LAN go2rtc / archived Playback path
- Temporary media cleaned on stop / idle TTL; auth + camera ACL enforced

### Centralized Command Center — CCC (RDSO 18.6.x)
Browser-only CCC at `/ccc`. Video never goes CCC → camera RTSP.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/ccc/capability` | Clause evidence |
| GET | `/api/ccc/sources` | Local VMS source (pluggable interface; no fake vendors) |
| GET | `/api/ccc/cameras` | ACL-filtered, paginated; streams not auto-started |
| GET | `/api/ccc/cameras/{id}/client-media` | Relative VMS media only |
| GET | `/api/ccc/status` | Reuses camera/user/session/recording/HA status |

Path: **CCC UI → VMS API/media → go2rtc/recordings**. Live selects subset tiles; Playback opens `/playback?camera=…`; Events reuse `/api/events`.

### CCC incidents / Event Log / SOP (RDSO 18.6.4, 18.6.7, 18.6.15.2, 18.6.20, 18.6.22.13–14)
Persistent incidents on Mongo `ccc_incidents` (ops layer on existing VMS events — **not** a second alarm engine).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/ccc/event-log` | Events + incident status/assignee; critical-first |
| GET/POST | `/api/ccc/incidents` | List / create (manual or `{event_id}` promote) |
| GET/PATCH | `/api/ccc/incidents/{id}` | Detail + multi-author updates / comments / status |
| POST | `/api/ccc/incidents/{id}/assign` | Manual responder / RPF redirect |
| POST | `/api/ccc/incidents/process-escalations` | Timed/priority/location escalation (assignment only) |
| GET/POST/PATCH/DELETE | `/api/ccc/sop-workflows` | Ordered SOP steps + escalation rules; **hot-reload** |

Duplicate promote of the same event → **409**. DMR/Tetra messaging is **out of scope**.

### CCC dashboard / Hot Screen / personalization (RDSO 18.6.5, 18.6.6, 18.6.15.4, 18.6.16.1)
Real metrics from cameras/events/incidents/storage/status — **no fabricated statistics**. Layout prefs are per-user (`ccc_dashboard_prefs`). Communications are **in-app CCC only** (sent/delivered/ack) — not SMS/email/DMR/Tetra.

**18.6.16.1 platform overview:** `GET /api/ccc/capability` → `platform_architecture` documents flexible/dynamic runtime config, distributed VMS HA + shared Mongo, poll-based reactive surfaces (not millisecond realtime), scalable pagination/virtualization (max 16 live tiles), IP browser/HTTPS/WSS paths, SOP/escalation/alarm workflows, and the single customized dashboard. UI entry: `/ccc`.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/ccc/dashboard` | Integrated metrics + prefs |
| GET/PUT | `/api/ccc/dashboard/prefs` | Per-user widget visibility/order |
| GET | `/api/ccc/hot-screen` | Critical queue; VMS live href only; camera ACL |
| GET/POST/PATCH | `/api/ccc/groups` | Ops groups (not RBAC roles) |
| GET/POST/PATCH | `/api/ccc/message-templates` | Reusable internal templates |
| POST | `/api/ccc/communications` | Log internal message vs incident |
| GET | `/api/ccc/incidents/{id}/communications` | List receipts |
| POST | `/api/ccc/communications/{id}/acknowledge` | In-app ack timestamp |
| GET | `/api/ccc/incidents/{id}/compliance` | SOP step pending/complete/overdue |

UI: `/ccc` Dashboard + Hot Screen tabs; poll refresh (~10–15s).

### CCC historical reports (RDSO 18.6.22.18)
Reuse persisted `ccc_incidents`, `events`, `audit_logs`, `ccc_communications` (+ derived SOP compliance). **No duplicate reporting collections.** Formats: JSON (paginated) and CSV/JSON download. Date filters honor **APP_TIMEZONE** for `YYYY-MM-DD`. No PDF framework added.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/ccc/reports` | Capability / kinds |
| GET | `/api/ccc/reports/incidents` | CCC incident history + ACL; `format=csv\|json` |
| GET | `/api/ccc/reports/events` | Event history via `list_events` ACL |
| GET | `/api/ccc/reports/activity` | Operator/audit logs (**Admin**) |
| GET | `/api/ccc/reports/communications` | Internal comms receipts |
| GET | `/api/ccc/reports/compliance` | SOP pending/complete/overdue |

UI: `/ccc?tab=reports`. Export actions audited as `CCC_REPORT_EXPORTED`.

### CCC devices / sensors / admin (RDSO 18.6.22.3, 18.6.22.12, 18.6.22.15)
Normalized registry `ccc_devices` (camera / VMS / camera digital input / external_sensor). External ingest authenticates with a bcrypt-hashed integration secret and creates events via existing `create_event` — **not** a second alarm engine. Pre-emption uses priority 1–5 (distinct from RBAC). No fake vendors; registration does not open streams.

| Method | Path | Notes |
|--------|------|-------|
| GET/POST | `/api/ccc/devices` | List (Events) / create (Admin) |
| GET/PATCH/DELETE | `/api/ccc/devices/{id}` | Detail / update / remove |
| POST | `/api/ccc/devices/{id}/ingest` | Alert/heartbeat via Bearer secret |
| POST | `/api/ccc/devices/{id}/heartbeat` | Status only |
| GET/PUT | `/api/ccc/admin/preemption-policy` | Priority pre-emption policy |
| GET | `/api/ccc/admin/gui-options` | Existing CCC GUI/RBAC surfaces |

UI: `/ccc?tab=devices`.

### CCC open third-party API (RDSO 18.6.22.8) — PARTIAL (GIS deferred)
One documented open CCC integration facade over **existing** device + VMS stacks. Events always go through `create_event` → CCC Event Log / Hot Screen / Incidents — **no second alarm engine**. No named vendor SDKs are bundled (`generic_rest` / device types only).

**GIS:** location / lat / lon may be preserved as **metadata** for future GIS. **GIS-format storage and Site Maps are not implemented** (`gis_format_storage: false`).

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/api/ccc/integrations/open/capability` | public | Common contract + limits |
| GET | `/api/ccc/integrations/open/identity` | public | System identity stamp |
| POST | `/api/ccc/integrations/open/register` | Admin | `kind=device\|vms` → existing registries |
| POST | `/api/ccc/integrations/open/health` | integration secret | Device heartbeat or VMS `health_status` |
| GET | `/api/ccc/integrations/open/devices` | Events | Lists devices + VMS integrations (secrets redacted) |
| POST | `/api/ccc/integrations/open/events` | integration secret | `target=device\|vms` → existing ingest |

Canonical implementations remain:
- `POST /api/ccc/devices/{id}/ingest` (+ `/heartbeat`)
- `POST /api/ccc/vms-integrations/{sourceId}/alerts`
- Admin: `/api/ccc/devices`, `/api/ccc/admin/vms-integrations`

**Event body (common):** `title`, `message`, `severity`, `priority`, `source_type`, `occurred_at`/`timestamp`, `location`/`zone`, optional `latitude`/`longitude`/`altitude`, `metadata`, `idempotency_key` or `external_event_id` (or `Idempotency-Key` header). Payload ≤ **16384** bytes.

Optional live/playback for external VMS uses the existing `VmsVideoSource` adapter when capabilities are enabled — never direct camera RTSP.

### CCC reliability + transport security (RDSO 18.6.22.1, 18.6.22.11)
- Startup: listen immediately; `/api/health` and `/api/ready` expose `startup_started_at`, `ready_at`, `startup_duration_seconds` (budget **300s**). API returns **503** until critical services (Mongo + indexes) are ready.
- Large data: CCC list/report APIs paginate with hard page caps; indexes on devices/events/incidents/audit; no full-collection loads for list UIs.
- Encryption: **TLS at Nginx** (`deploy/nginx-cctv-tls.sample.conf`) — TLS 1.2/1.3, ≥128-bit AEAD ciphers. Backend stays localhost HTTP. Set `HTTPS_ENFORCE=1` and `X-Forwarded-Proto: https` in production. Session cookies: HttpOnly + SameSite=Lax; Secure when HTTPS / `SESSION_COOKIE_SECURE=1`. Media paths are relative (`/media/wN/...`) so browsers use WSS on HTTPS pages.
- Capability: `GET /api/ccc/security`, clauses `18.6.22.1` / `18.6.22.11` on `/api/ccc/capability`.
- Opt-in acceptance: `RDSO_18_6_22_ACCEPTANCE=1 python backend/scripts/rdso_18_6_22_1_11_accept_startup_tls.py` (synthetic data; no fleet streams).

### VMS management-server HA (N:1) — 18.1.29
| Method | Path | Auth |
|--------|------|------|
| GET | `/api/vms/ha/ready` | public |
| GET | `/api/vms/ha/status` | super-admin |
| GET | `/api/vms/ha/nodes` | super-admin |
| POST | `/api/vms/ha/nodes` | super-admin |
| POST | `/api/vms/ha/heartbeat` | super-admin |

Enable with `VMS_HA_ENABLED=true` and distinct `VMS_SERVER_ID` / `VMS_SERVER_ROLE` per VMS process sharing the same MongoDB. Singleton jobs (schedule monitor, motion poller) run only on the coordinator lease holder. Does **not** replace recording HA — reuse 18.3.3 for NVR redundancy. Sessions already Mongo-backed; clients keep relative API/media paths.

## Machine-readable schema

`GET /api/docs/integration/openapi.json`

## Security notes for integrators

- Do not expect camera passwords in JSON responses
- Store device credentials only in the VMS; exchange session tokens for API access
- Respect camera ACL — `403` means the user cannot access that camera

## Certification (supply acceptance)

Software ONVIF Profile S/G interoperability and the REST integration package satisfy the **software**
intent of RDSO **18.1.30**. Accredited **ONVIF Client List** / third-party interoperability
laboratory certification (if required by the purchaser or RDSO supply checklist) remains a
**separate external acceptance item** and is not claimed solely by this software package.
