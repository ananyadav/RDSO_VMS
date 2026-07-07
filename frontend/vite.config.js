// Note: Vite currently resolves the JS config (`vite.config.js`) in this repo.
// Keep this file in sync or remove it if not needed to avoid confusion.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/** Benign proxy errors during backend restart or stream teardown — avoid log spam. */
const BENIGN_PROXY_CODES = new Set(['ECONNABORTED', 'ECONNRESET', 'EPIPE', 'ECONNREFUSED']);

function configureDevProxy(proxy, label) {
    proxy.on('error', (err, _req, res) => {
        const code = err && err.code;
        if (code && BENIGN_PROXY_CODES.has(code)) {
            if (
                res &&
                typeof res.writeHead === 'function' &&
                !res.headersSent &&
                !res.writableEnded
            ) {
                res.writeHead(503, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: 'Backend unavailable — retry shortly' }));
            }
            return;
        }
        console.warn(`[vite] ${label} proxy:`, err.message || err);
    });
}

// https://vitejs.dev/config/
export default defineConfig({
    base: '/',
    appType: 'spa',
    plugins: [react()],
    server: {
        host: "0.0.0.0",
        port: 3000,
        proxy: {
            // Proxy API requests to the aiohttp server
            '/api': {
                target: 'http://127.0.0.1:10000',
                changeOrigin: true,
                configure: (proxy) => configureDevProxy(proxy, 'api'),
            },
            // Proxy WebSocket connections
            '/ws': {
                target: 'ws://127.0.0.1:10000',
                ws: true,
                changeOrigin: true,
                configure: (proxy) => configureDevProxy(proxy, 'ws'),
            },
            // go2rtc stream proxy — must NOT match React route /go2rtc-diagnostics
            '/go2rtc': {
                target: 'http://127.0.0.1:10000',
                changeOrigin: true,
                ws: true,
                configure: (proxy) => configureDevProxy(proxy, 'go2rtc'),
                bypass(req) {
                    const path = (req.url ?? '').split('?')[0];
                    // Let Vite serve the SPA (dev uses /src/main.tsx, not /assets/*)
                    if (path === '/go2rtc-diagnostics') {
                        return '/index.html';
                    }
                },
            },
        },
    },
});
