#!/bin/bash
# GPU / Linux production deploy — build frontend, sync static, PM2 restart, verify go2rtc workers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Building frontend..."
cd frontend
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run build
cd "$ROOT"

echo "==> Copying frontend build to backend/static..."
mkdir -p backend/static
rm -rf backend/static/*
cp -r frontend/dist/* backend/static/

echo "==> Restarting PM2 processes..."
if ! command -v pm2 >/dev/null 2>&1; then
  echo "ERROR: pm2 not found. Install: npm i -g pm2"
  exit 1
fi

# WebRTC must advertise the GPU server LAN IP (not 127.0.0.1) for remote browsers.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
if [ -z "${GO2RTC_WEBRTC_HOST:-}" ]; then
  GO2RTC_WEBRTC_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
  export GO2RTC_WEBRTC_HOST
  echo "==> GO2RTC_WEBRTC_HOST not set — using ${GO2RTC_WEBRTC_HOST}"
fi

pm2 startOrReload ecosystem.config.cjs --update-env

echo "==> Waiting for backend..."
sleep 5

echo "==> Verifying go2rtc workers..."
python3 backend/scripts/ensure_go2rtc_workers.py

echo "==> PM2 status:"
pm2 list

echo ""
echo "Deploy complete. Open http://$(hostname -I | awk '{print $1}'):10000 (or your configured host)."
