# NVR Interface

Web-based Network Video Recorder (NVR) for managing IP cameras: live HLS streaming, recording, playback, users, and system monitoring.

## Project structure

```
CCTV/
├── frontend/          # React + Vite + TypeScript UI
│   ├── src/
│   ├── public/
│   └── package.json
├── backend/           # Python aiohttp API + FFmpeg streaming
│   ├── app/           # Application code (routes, services, core)
│   ├── scripts/       # CLI utilities (add user, diagnose network, …)
│   ├── tests/
│   ├── config.yaml
│   ├── requirements.txt
│   └── static/        # Production build output (generated)
├── docs/              # Documentation
├── Recordings/        # HLS recording buffer (generated at runtime)
├── start_production.sh
├── ecosystem.config.cjs
└── nvr-interface.service
```

## Prerequisites

- **Node.js** 20+ and npm
- **Python** 3.8+
- **FFmpeg**
- **MongoDB** 4.0+

## Quick start (development)

**Windows (recommended):** one command starts backend + frontend and opens the UI:

```powershell
.\start_dev.ps1
```

Then use **http://127.0.0.1:3000/** — do **not** use `localhost:3000` (Cursor IDE hijacks that port on some machines).

### Manual start

1. Copy `.env.example` → `.env` and set your **MongoDB Atlas** URI (production fleet ~793 cameras).
2. Backend (from `backend/`):

```powershell
python -m app.main --api-port 10000
```

Wait until you see `Startup complete` in the log (Atlas can take 1–2 minutes).

3. Frontend (second terminal, from `frontend/`):

```powershell
npm install
npm run dev
```

Open **http://127.0.0.1:3000/** — login: `admin123` / `admin123`.

The UI waits for `/api/health` before loading cameras or locations, so you should not see empty dropdowns while the backend is still starting.

## Production (single port)

From the project root:

```sh
chmod +x start_production.sh
./start_production.sh
```

This builds `frontend/`, copies assets to `backend/static/`, and starts the backend on port **10000**.

## Configuration

| Item | Location |
|------|----------|
| MongoDB URI | `.env` → `MONGODB_URI` or `backend/app/config.py` |
| Cameras (optional YAML) | `backend/config.yaml` |
| Recording directory | `.env` → `RECORDINGS_DIR` (default: `./Recordings`) |

## Scripts

| Command | Purpose |
|---------|---------|
| `python -m backend.app.main --api-port 10000` | Run API server |
| `cd frontend && npm run dev` | Frontend dev server |
| `cd frontend && npm run build` | Production frontend build |
| `python backend/scripts/add_user.py` | Add a user (see script) |
| `python backend/scripts/diagnose_network.py` | Network / camera diagnostics |

## API overview

- `GET/POST /api/cameras` — camera CRUD and scan
- `POST /api/login` — authentication
- `GET/POST /api/recordings/*` — recording schedule and HLS segments
- `GET/POST /api/go2rtc/*` — go2rtc worker status, sync, diagnostics
- `GET /go2rtc/api/ws` — WebRTC/MSE live view (all workers, per-camera routing)
- `GET /api/health` — backend readiness (MongoDB + migrations)
- `GET /api/status` — system health

More detail: [docs/RUNNING.md](docs/RUNNING.md)  
Live streaming baseline: [docs/STREAMING_BASELINE.md](docs/STREAMING_BASELINE.md)  
Live View QA checklist: [docs/LIVE_VIEW_TEST_CHECKLIST.md](docs/LIVE_VIEW_TEST_CHECKLIST.md)

## License

MIT (see LICENSE when added).
