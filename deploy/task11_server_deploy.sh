#!/usr/bin/env bash
# Task 11 — server-side inspect, backup, nginx direct-media ensure (Linux).
# Invoked from production.yml over SSH. Does NOT change camera settings or recording.
set -euo pipefail

ROOT="${SERVER_DEPLOY_PATH:-/home/vms/cctv_ananya/CCTV}"
DOMAIN_IP="${PUBLIC_BIND_IP:-192.168.17.150}"
PM2_NAME="${PM2_BACKEND_NAME:-cctv-backend-new}"
API_PORT="${API_PORT:-10000}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_ROOT="${ROOT}/../CCTV-backups"
BACKUP_DIR="${BACKUP_ROOT}/task11-${TS}"
REPORT="${ROOT}/deploy/task11-deploy-report.json"

cd "$ROOT"
mkdir -p "$BACKUP_DIR" "$(dirname "$REPORT")" deploy

echo "=== INSPECT ==="
echo "ROOT=$ROOT"
echo "HOST=$(hostname)"
echo "USER=$(whoami)"
echo "GIT=$(git rev-parse HEAD 2>/dev/null || echo none)"
echo "PM2_NAME=$PM2_NAME"
pm2 describe "$PM2_NAME" >/dev/null 2>&1 && pm2 describe "$PM2_NAME" | sed -n '1,40p' || echo "PM2 process missing"
ss -lntp 2>/dev/null | grep -E ':(80|443|3000|10000|1984|1985|1986)\s' || netstat -lntp 2>/dev/null | grep -E ':(80|443|3000|10000|1984|1985|1986)\s' || true
echo "--- .env keys (names only) ---"
if [[ -f .env ]]; then
  grep -E '^[A-Z0-9_]+=' .env | cut -d= -f1 | sort
else
  echo "NO .env"
fi
echo "--- static ---"
ls -la backend/static/index.html 2>/dev/null || echo "missing static"
echo "--- go2rtc workers ---"
ls -la go2rtc/workers/*/go2rtc.yaml 2>/dev/null || true
for p in 1984 1985 1986; do
  if curl -fsS --max-time 2 "http://127.0.0.1:${p}/api" >/dev/null 2>&1; then
    echo "worker port ${p}: UP"
  else
    echo "worker port ${p}: down-or-auth"
  fi
done
echo "--- nginx ---"
nginx -v 2>&1 || true
ls /etc/nginx/sites-enabled 2>/dev/null || ls /etc/nginx/conf.d 2>/dev/null || true

echo "=== BACKUP ==="
# App tree snapshot without heavy/local dirs
tar --exclude='./Recordings' --exclude='./recordings' --exclude='./node_modules' \
  --exclude='./frontend/node_modules' --exclude='./.venv' --exclude='./venv' \
  --exclude='./.git' --exclude='./deploy/nginx-win' --exclude='./__pycache__' \
  -czf "${BACKUP_DIR}/app-tree.tgz" -C "$ROOT" . 2>/dev/null || \
  tar -czf "${BACKUP_DIR}/app-tree.tgz" -C "$ROOT" backend/static .env 2>/dev/null || true
cp -a .env "${BACKUP_DIR}/env.backup" 2>/dev/null || true
cp -a backend/static "${BACKUP_DIR}/static.backup" 2>/dev/null || true
if [[ -d /etc/nginx ]]; then
  sudo cp -a /etc/nginx "${BACKUP_DIR}/nginx" 2>/dev/null || cp -a /etc/nginx "${BACKUP_DIR}/nginx" 2>/dev/null || true
fi
if [[ -d go2rtc ]]; then
  mkdir -p "${BACKUP_DIR}/go2rtc"
  cp -a go2rtc/workers "${BACKUP_DIR}/go2rtc/" 2>/dev/null || true
fi
pm2 save 2>/dev/null || true
cp -a "${HOME}/.pm2/dump.pm2" "${BACKUP_DIR}/pm2.dump.pm2" 2>/dev/null || true
echo "BACKUP_DIR=${BACKUP_DIR}"
ls -la "$BACKUP_DIR" || true

echo "=== NGINX DIRECT MEDIA ==="
# Detect actual worker API ports (default 1984/1985/1986)
W1=1984; W2=1985; W3=1986
SITE_AVAILABLE="/etc/nginx/sites-available/cctv-direct-media"
SITE_ENABLED="/etc/nginx/sites-enabled/cctv-direct-media"
CONF_D="/etc/nginx/conf.d/cctv-direct-media.conf"

write_nginx() {
  local dest="$1"
  cat >"$dest" <<EOF
# Task 11 — CCTV direct media (generated ${TS})
upstream cctv_backend {
    server 127.0.0.1:${API_PORT};
    keepalive 32;
}

map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

map \$go2rtc_worker_id \$go2rtc_upstream {
    default http://127.0.0.1:${W1};
    1       http://127.0.0.1:${W1};
    2       http://127.0.0.1:${W2};
    3       http://127.0.0.1:${W3};
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${DOMAIN_IP} _;
    client_max_body_size 100M;

    location /api/ {
        proxy_pass http://cctv_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Cookie \$http_cookie;
        proxy_read_timeout 300s;
    }

    location /go2rtc/ {
        proxy_pass http://cctv_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Cookie \$http_cookie;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }

    location = /internal/go2rtc-media-auth {
        internal;
        proxy_pass http://cctv_backend/api/go2rtc/media-auth\$is_args\$args;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie \$http_cookie;
        proxy_set_header X-Original-URI \$request_uri;
    }

    location ~ ^/media/w([0-9]+)/(.*)\$ {
        set \$go2rtc_worker_id \$1;
        set \$go2rtc_path \$2;
        auth_request /internal/go2rtc-media-auth;
        proxy_pass \$go2rtc_upstream/\$go2rtc_path\$is_args\$args;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_buffering off;
    }

    location / {
        proxy_pass http://cctv_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header Cookie \$http_cookie;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
}

TMP_NGINX="${ROOT}/deploy/cctv-direct-media.generated.conf"
write_nginx "$TMP_NGINX"
cp -a "$TMP_NGINX" "${BACKUP_DIR}/cctv-direct-media.generated.conf"

NGINX_INSTALLED=0
can_sudo() {
  # Passwordless sudo only — CI user often has no TTY for sudo password
  sudo -n true >/dev/null 2>&1
}

verify_media_routes() {
  local ok=1
  for w in 1 2 3; do
    # Logged-out must not be open (401/403). Reachability of the route itself matters.
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/media/w${w}/api" || echo 000)"
    echo "local /media/w${w}/api -> HTTP ${code}"
    if [[ "$code" == "000" || "$code" == "404" || "$code" == "502" || "$code" == "503" ]]; then
      ok=0
    fi
  done
  return $((1 - ok))
}

if command -v nginx >/dev/null 2>&1; then
  NGINX_INSTALLED=1
  if can_sudo; then
    # Prefer sites-available pattern; fall back to conf.d
    if [[ -d /etc/nginx/sites-available ]]; then
      sudo -n cp "$TMP_NGINX" "$SITE_AVAILABLE"
      sudo -n ln -sfn "$SITE_AVAILABLE" "$SITE_ENABLED"
      # Disable stock default only (often proxies wrong upstream)
      if [[ -e /etc/nginx/sites-enabled/default ]]; then
        sudo -n mv /etc/nginx/sites-enabled/default "${BACKUP_DIR}/disabled-site-default" 2>/dev/null \
          || sudo -n rm -f /etc/nginx/sites-enabled/default || true
      fi
    elif [[ -d /etc/nginx/conf.d ]]; then
      sudo -n cp "$TMP_NGINX" "$CONF_D"
    else
      echo "WARN: nginx present but no sites-available/conf.d"
    fi

    if sudo -n nginx -t; then
      sudo -n systemctl reload nginx || sudo -n nginx -s reload
      echo "Nginx reloaded OK"
    else
      echo "ERROR: nginx -t failed — NOT reloading"
      exit 1
    fi
  else
    echo "WARN: passwordless sudo unavailable — leaving existing Nginx config untouched"
    echo "Generated reference config at: ${TMP_NGINX}"
    if verify_media_routes; then
      echo "Existing /media/wN routes look healthy (no Nginx rewrite attempted)"
    else
      echo "ERROR: /media/wN routes not healthy and cannot update Nginx without sudo"
      echo "Ask an admin to install ${TMP_NGINX} (see deploy/nginx-cctv-direct-media.conf) then: nginx -t && systemctl reload nginx"
      exit 1
    fi
  fi
else
  echo "WARN: nginx binary not found"
fi

echo "=== ENSURE APP ==="
# Backend should already be restarted by production.yml; ensure health
ok=0
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done
HEALTH="$(curl -fsS "http://127.0.0.1:${API_PORT}/api/health" || echo '{"ready":false}')"
echo "HEALTH=${HEALTH}"

# Ensure go2rtc workers if script exists
if [[ -f backend/scripts/ensure_go2rtc_workers.py ]]; then
  python3 backend/scripts/ensure_go2rtc_workers.py || true
fi

# Recording must stay stopped — do not start recorders
echo "Recording: intentionally not started"

# Write minimal JSON report
python3 - <<PY
import json, os, socket
report = {
  "timestamp": "${TS}",
  "root": "${ROOT}",
  "backup_dir": "${BACKUP_DIR}",
  "hostname": socket.gethostname(),
  "git": "$(git rev-parse HEAD 2>/dev/null || echo none)",
  "api_port": ${API_PORT},
  "domain_ip": "${DOMAIN_IP}",
  "nginx_installed": bool(${NGINX_INSTALLED}),
  "health_raw": '''${HEALTH}''',
  "worker_ports": {"w1": ${W1}, "w2": ${W2}, "w3": ${W3}},
  "pm2_name": "${PM2_NAME}",
}
open("${REPORT}", "w", encoding="utf-8").write(json.dumps(report, indent=2))
print("Wrote ${REPORT}")
print(json.dumps(report, indent=2))
PY

echo "=== TASK11 SERVER SCRIPT DONE ==="
