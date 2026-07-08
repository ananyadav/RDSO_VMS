// Note: Vite currently resolves the JS config (`vite.config.js`) in this repo.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import http from 'node:http';

/** Benign proxy errors during backend restart or stream teardown — avoid log spam. */
const BENIGN_PROXY_CODES = new Set(['ECONNABORTED', 'ECONNRESET', 'EPIPE', 'ECONNREFUSED']);

const BACKEND_ORIGIN = 'http://127.0.0.1:10000';

function isHttpResponse(res) {
    return Boolean(res && typeof res.writeHead === 'function' && typeof res.end === 'function');
}

function configureHttpProxy(proxy, label) {
    proxy.on('error', (err, _req, res) => {
        const code = err && err.code;
        if (code && BENIGN_PROXY_CODES.has(code)) {
            if (isHttpResponse(res) && !res.headersSent && !res.writableEnded) {
                res.writeHead(503, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: 'Backend unavailable — retry shortly' }));
            }
            return;
        }
        console.warn(`[vite] ${label} proxy:`, err.message || err);
    });
}

/** WS proxy passes a socket as the third arg — never call writeHead on it. */
function configureWsProxy(proxy) {
    proxy.on('error', (err) => {
        const code = err && err.code;
        if (code && BENIGN_PROXY_CODES.has(code)) {
            return;
        }
        console.warn('[vite] ws proxy:', err.message || err);
    });
}

/**
 * Proxy /go2rtc/* to the backend before Vite tries to resolve those URLs as local files.
 * Fixes: "Pre-transform error: Failed to load url /go2rtc/video-stream.js"
 */
function go2rtcEarlyProxyPlugin() {
    return {
        name: 'go2rtc-early-proxy',
        apply: 'serve',
        configureServer(server) {
            const handler = (req, res, next) => {
                const path = (req.url ?? '').split('?')[0];
                if (!path.startsWith('/go2rtc') || path === '/go2rtc-diagnostics') {
                    return next();
                }
                if ((req.headers.upgrade || '').toLowerCase() === 'websocket') {
                    return next();
                }

                const proxyReq = http.request(
                    {
                        hostname: '127.0.0.1',
                        port: 10000,
                        path: req.url,
                        method: req.method,
                        headers: {
                            ...req.headers,
                            host: '127.0.0.1:10000',
                        },
                    },
                    (proxyRes) => {
                        if (isHttpResponse(res) && !res.headersSent) {
                            res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
                            proxyRes.pipe(res);
                        }
                    },
                );

                proxyReq.on('error', (err) => {
                    const code = err && err.code;
                    if (code && BENIGN_PROXY_CODES.has(code)) {
                        if (isHttpResponse(res) && !res.headersSent) {
                            res.writeHead(503, { 'Content-Type': 'application/json' });
                            res.end(JSON.stringify({ error: 'Backend unavailable — retry shortly' }));
                        }
                        return;
                    }
                    next(err);
                });

                req.pipe(proxyReq);
            };

            // Registered before Vite internal middleware — go2rtc assets are not pre-bundled.
            server.middlewares.use(handler);
        },
    };
}

// https://vitejs.dev/config/
export default defineConfig({
    base: '/',
    appType: 'spa',
    plugins: [react(), go2rtcEarlyProxyPlugin()],
    server: {
        host: '0.0.0.0',
        port: 3000,
        proxy: {
            '/api': {
                target: BACKEND_ORIGIN,
                changeOrigin: true,
                configure: (proxy) => configureHttpProxy(proxy, 'api'),
            },
            // WebSocket + HTTP for go2rtc live view (and PTZ).
            '/go2rtc': {
                target: BACKEND_ORIGIN,
                changeOrigin: true,
                ws: true,
                configure: configureWsProxy,
                bypass(req) {
                    const path = (req.url ?? '').split('?')[0];
                    if (path === '/go2rtc-diagnostics') {
                        return '/index.html';
                    }
                },
            },
        },
    },
});
