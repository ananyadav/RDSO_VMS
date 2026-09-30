# RDSO recording platform / network / capacity (18.3.4 · 18.3.7 · 18.3.10)

## 18.3.4 — Open architecture

Recording software runs on **standard Windows or Linux x86** hosts. It does **not** require proprietary NVR/server hardware.

Dependencies (all open / commodity):

| Component | Role |
|-----------|------|
| FFmpeg | RTSP ingest and HLS recording |
| MongoDB | Metadata and configuration |
| OS filesystem | Recording storage (local or host-mounted DAS/NAS/SAN) |
| RTSP / ONVIF | Camera interoperability |

Evidence API: `GET /api/recordings/capability` → `platform.open_architecture`.

## 18.3.7 — Network access from any location

Recording management and status are available through the **authenticated VMS HTTP API**. Typical deployment:

`Client (any network location) → Nginx → Backend /api/*`

- Application routes do **not** restrict callers to localhost.
- Default `API_HOST=127.0.0.1` is intentional when Nginx fronts the API on the LAN/WAN.
- Session auth, RBAC, and camera ACL remain enforced.

Evidence API: `GET /api/recordings/capability` → `platform.network_access`.

## 18.3.10 — Recording capacity status

Capacity is reported from the configured recordings volume (18.1.12 probe; no silent drive fallback):

- total / used / free (GB and bytes)
- percent used / percent free
- status: **Online** | **Low Space** | **Critical** | **Unavailable** (plus Read Only)

UI: Storage → Recordings Volume (`/api/storage/dashboard`).  
Also mirrored on `GET /api/recordings/capability` → `recording_capacity_status`.

---

## 18.1.17 / 18.1.19 / 18.1.20 � Multi-server and seamless clients

### 18.1.17 � Unlimited networked recording servers

- Mongo `recording_servers` registry accepts arbitrary `server_id` entries.
- `RECORDING_SERVER_COUNT_LIMIT = None` (no software cap).
- Management API: `GET /api/recordings/ha/servers` (and `GET .../ha/status`).
- Does not fabricate physical hosts � only registered logical servers.

### 18.1.19 � Dynamic client camera connection

- Live View loads cameras via `GET /api/cameras` and switches tiles without restart.
- Live media uses relative Nginx paths `/media/w{workerId}/api/ws` (go2rtc workers).
- Playback opens sessions dynamically via camera-based search + relative HLS paths.

### 18.1.20 � Seamless regardless of recording server

- Clients never need recording-server host URLs or credentials.
- Playback search filters by camera identity only (`recording_server_id` ignored).
- HA home/owner reassignment keeps the same `camera_id` / `camera_uid`.
- Descriptor: `GET /api/cameras/{id}/client-media` (auth + camera ACL).

**Deployment-only:** Multi-host HA still needs shared `RECORDINGS_DIR` (or equivalent) so the API host can serve media files produced by any recording server.
