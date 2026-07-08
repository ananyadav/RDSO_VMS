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

### 1. MongoDB

Ensure MongoDB is running (e.g. `mongodb://localhost:27017`).

Optional: create a `.env` file in the **project root**:

```env
MONGODB_URI=mongodb://localhost:27017
RECORDINGS_DIR=./Recordings
```

### 2. Backend

```sh
pip install -r backend/requirements.txt
python -m backend.app.main --api-port 10000
```

API and WebSocket: `http://localhost:10000` (same port).

### 3. Frontend

In a second terminal:

```sh
cd frontend
npm install
npm run dev
```

Dev UI: `http://localhost:3000` (proxies `/api` and `/go2rtc` to the backend).

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
- `GET /api/status` — system health

More detail: [docs/RUNNING.md](docs/RUNNING.md)  
Live streaming baseline: [docs/STREAMING_BASELINE.md](docs/STREAMING_BASELINE.md)  
Live View QA checklist: [docs/LIVE_VIEW_TEST_CHECKLIST.md](docs/LIVE_VIEW_TEST_CHECKLIST.md)

## License

MIT (see LICENSE when added).
