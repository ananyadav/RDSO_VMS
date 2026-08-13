#!/usr/bin/env bash
# Task 12A — verify backend binds 127.0.0.1:10000 only (Linux server).
set -euo pipefail

ROOT="${SERVER_DEPLOY_PATH:-/home/vms/cctv_ananya/CCTV}"
API_PORT="${API_PORT:-10000}"
API_HOST="${API_HOST:-127.0.0.1}"
PM2_NAME="${PM2_BACKEND_NAME:-cctv-backend-new}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${ROOT}/../CCTV-backups/task12a-${TS}"
REPORT="${ROOT}/deploy/task12a-verify-report.json"

mkdir -p "$BACKUP_DIR" "$(dirname "$REPORT")"

echo "=== TASK 12A BACKUP ==="
cp -a "${ROOT}/backend/app/main.py" "${BACKUP_DIR}/main.py.bak" 2>/dev/null || true
if [[ -f "${ROOT}/.env" ]]; then
  grep -E '^API_HOST=' "${ROOT}/.env" >"${BACKUP_DIR}/env-api-host.snip" 2>/dev/null || true
fi
echo "BACKUP_DIR=${BACKUP_DIR}"

echo "=== SOCKET :${API_PORT} ==="
SS_OUT="$(ss -ltnp 2>/dev/null | grep ":${API_PORT}" || true)"
echo "${SS_OUT}"

if echo "${SS_OUT}" | grep -qE '0\.0\.0\.0:'"${API_PORT}"'|\[::\]:'"${API_PORT}"'|\*:'"${API_PORT}"; then
  echo "ERROR: backend still listening on all interfaces"
  exit 1
fi
if ! echo "${SS_OUT}" | grep -q "127.0.0.1:${API_PORT}"; then
  echo "ERROR: 127.0.0.1:${API_PORT} not found in ss output"
  exit 1
fi
echo "OK: localhost bind confirmed"

echo "=== LOCALHOST HEALTH ==="
HEALTH="$(curl -fsS "http://127.0.0.1:${API_PORT}/api/health")"
echo "${HEALTH}"

python3 - <<PY
import json, os, socket
report = {
  "timestamp": "${TS}",
  "backup_dir": "${BACKUP_DIR}",
  "api_host": "${API_HOST}",
  "api_port": int("${API_PORT}"),
  "pm2_name": "${PM2_NAME}",
  "ss_lines": """${SS_OUT}""".strip().splitlines(),
  "health_raw": """${HEALTH}""",
  "hostname": socket.gethostname(),
}
open("${REPORT}", "w", encoding="utf-8").write(json.dumps(report, indent=2))
print("Wrote ${REPORT}")
print(json.dumps(report, indent=2))
PY

echo "=== TASK 12A VERIFY DONE ==="
