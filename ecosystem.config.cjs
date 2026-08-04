const path = require('path');
const root = __dirname;

/** Linux servers usually have python3 only; Windows dev typically uses python. */
const pythonBin = process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3');

module.exports = {
  apps: [
    {
      name: 'cctv-backend',
      cwd: root,
      script: pythonBin,
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
    // go2rtc workers are NOT listed here. The backend writes
    // go2rtc/workers/{N}/go2rtc.yaml then starts/reloads them via PM2
    // (startup_workers / pm2_start_worker). That avoids crash-loops when
    // YAML does not exist yet on a fresh host.
  ],
};
