# GPU Server Production Deploy

Deploy the CCTV NVR stack on a Linux GPU server with PM2, go2rtc workers, and a single-port UI.

## Prerequisites

- Linux with NVIDIA GPU (optional; set `VIDEO_HWACCEL=cuda` for FFmpeg)
- Node.js 18+ and Python 3.10+
- PM2: `npm install -g pm2`
- go2rtc binary at `go2rtc/bin/go2rtc` (Linux amd64 from [go2rtc releases](https://github.com/AlexxIT/go2rtc/releases))
- MongoDB reachable from the server

## 1. Configure `.env`

Copy `.env.example` to `.env` and set at minimum:

```env
MONGODB_URI=mongodb+srv://...
GO2RTC_ENABLED=true
GO2RTC_WORKERS_ENABLED=true
GO2RTC_MAX_CAMERAS_PER_WORKER=300
GO2RTC_MANAGED_BY=pm2
GO2RTC_WEBRTC_HOST=<GPU_SERVER_LAN_IP>
```

**First deploy:** leave `SKIP_STARTUP_MIGRATIONS` unset so DB migrations run once.

**Later restarts:** add `SKIP_STARTUP_MIGRATIONS=1` for faster boots.

Do **not** set `LIVE_PROVIDER` — live view is go2rtc only.

Optional CORS for dev frontend on another origin:

```env
CORS_ORIGINS=http://<your-pc-ip>:3000
```

## 2. Firewall

Open on the GPU server:

| Port | Purpose |
|------|---------|
| 10000 | Backend API + UI (HTTP/WebSocket) |
| 8555, 8557, 8559 | WebRTC UDP per go2rtc worker (workers 1–3) |
| 1984, 1985, 1986 | go2rtc API (localhost only — do not expose publicly) |

Worker N WebRTC port = `8555 + 2*(N-1)`.

## 3. Deploy

```bash
chmod +x deploy_production.sh
./deploy_production.sh
```

This will:

1. Build the frontend (`npm ci` + `vite build`)
2. Copy `frontend/dist/` → `backend/static/`
3. `pm2 startOrReload ecosystem.config.cjs`
4. Run `backend/scripts/ensure_go2rtc_workers.py`

## 4. Verify

```bash
pm2 list                    # cctv-backend, go2rtc-worker-1, go2rtc-worker-2 online
python3 backend/scripts/ensure_go2rtc_workers.py
python3 backend/scripts/smoke_test_system.py
python3 backend/scripts/diagnose_go2rtc_drift.py
```

Open `http://<GPU_IP>:10000` — hard-refresh after deploy.

## 5. Scaling beyond 600 cameras

- Worker 3+ is started automatically by the backend when cameras exceed 300/worker
- Run `pm2 save` after new workers appear so they survive reboot
- Add worker N to `ecosystem.config.cjs` once you know you need a fixed fleet size

## 6. Secrets hygiene

Never commit:

- `.env`
- `go2rtc/runtime/go2rtc.yaml`
- `go2rtc/workers/*/go2rtc.yaml`

These files contain RTSP credentials and are listed in `.gitignore`.

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| 278 cameras offline in diagnostics | Worker 2 not running — `pm2 restart go2rtc-worker-2` or `./deploy_production.sh` |
| Live view black remotely | Set `GO2RTC_WEBRTC_HOST` to GPU LAN IP; open UDP 8555/8557 |
| Slow backend restart | `SKIP_STARTUP_MIGRATIONS=1` in `.env` |
| Storage page 403 | User needs **System** permission or Admin role |
