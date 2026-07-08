# go2rtc — multi-worker live streaming

Live View uses **go2rtc workers** (not a single monolithic process).

## Architecture

- **Worker 1**: `go2rtc/workers/1/go2rtc.yaml` → API `:1984`, WebRTC `:8555`
- **Worker 2**: `go2rtc/workers/2/go2rtc.yaml` → API `:1985`, WebRTC `:8557`
- **Worker 3**: `go2rtc/workers/3/go2rtc.yaml` → API `:1986`, WebRTC `:8559`
- **Do not** start `go2rtc/runtime/go2rtc.yaml` — it conflicts with worker 1 on port 1984.

## Production (PM2)

```bash
./deploy_production.sh
# or
pm2 start ecosystem.config.cjs
python backend/scripts/ensure_go2rtc_workers.py
```

## Local dev

Backend starts workers as subprocesses when PM2 is unavailable:

```bash
python -m backend.app.main --api-port 10000
python backend/scripts/ensure_go2rtc_workers.py
```

## Diagnostics

- UI: **Sidebar → go2rtc Diagnostics** (`/go2rtc-diagnostics`)
- CLI: `python backend/scripts/ensure_go2rtc_workers.py`
- Per-IP: `python backend/scripts/diagnose_ips.py 192.168.x.x`
- Unreachable list: `python backend/scripts/list_unreachable_cameras.py`

## Env (see `.env.example`)

- `GO2RTC_WORKERS_ENABLED=true`
- `GO2RTC_MAX_CAMERAS_PER_WORKER=300`
- `GO2RTC_WEBRTC_HOST=<server LAN IP>` (auto-detected if unset)
- `GO2RTC_ENABLED=true`

Download binary: [AlexxIT/go2rtc releases](https://github.com/AlexxIT/go2rtc/releases) → `go2rtc/bin/`
