#!/bin/bash
# Legacy single-process startup (no PM2). For GPU production use ./deploy_production.sh instead.

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "NOTE: For production with go2rtc workers, run: ./deploy_production.sh"

echo "Building frontend..."
cd frontend
npm install
npm run build

echo "Copying frontend build to backend/static..."
cd "$ROOT"
mkdir -p backend/static
cp -r frontend/dist/* backend/static/

echo "Starting backend server..."
python3 -m backend.app.main --api-port 10000

echo "Server started. Open http://localhost:10000 in your browser."
