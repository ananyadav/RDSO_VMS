#!/bin/bash

# NVR Interface Production Startup Script
# Builds the frontend and starts the backend (serves API + static UI on one port)

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

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
