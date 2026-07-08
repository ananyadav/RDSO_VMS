const path = require('path');
const root = __dirname;

const go2rtcBin = path.join(root, 'go2rtc', 'bin', 'go2rtc');

module.exports = {
  apps: [
    {
      name: 'cctv-backend',
      cwd: root,
      script: 'python',
      args: '-m backend.app.main --api-port 10000',
      autorestart: true,
      watch: false,
      max_memory_restart: '500M',
    },
    {
      name: 'cctv-frontend-dev',
      cwd: path.join(root, 'frontend'),
      script: 'npm',
      args: 'run dev',
      interpreter: 'none',
      autorestart: true,
      watch: false,
    },
    // go2rtc worker 1 — worker 2+ configs are written by the backend on startup/sync.
    {
      name: 'go2rtc-worker-1',
      cwd: root,
      script: go2rtcBin,
      args: '-config go2rtc/workers/1/go2rtc.yaml',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
    },
    // Worker 2 — required when active cameras exceed GO2RTC_MAX_CAMERAS_PER_WORKER (default 300).
    // Config file is created by backend before first start; PM2 may restart until it exists.
    // Worker 3 — when active cameras exceed 600 (3 × GO2RTC_MAX_CAMERAS_PER_WORKER default 300).
    {
      name: 'go2rtc-worker-3',
      cwd: root,
      script: go2rtcBin,
      args: '-config go2rtc/workers/3/go2rtc.yaml',
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
    },
  ],
};
