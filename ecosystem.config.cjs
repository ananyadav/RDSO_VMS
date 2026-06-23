const path = require('path');
const root = __dirname;

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
  ],
};
