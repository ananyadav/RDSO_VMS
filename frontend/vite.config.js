// Note: Vite currently resolves the JS config (`vite.config.js`) in this repo.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import http from 'node:http';
import os from 'node:os';

/** Benign proxy errors during backend restart or stream teardown — avoid log spam. */
const BENIGN_PROXY_CODES = new Set(['ECONNABORTED', 'ECONNRESET', 'EPIPE', 'ECONNREFUSED']);

/**
 * Cursor IDE binds 127.0.0.1:10000 on Windows and intercepts API calls meant for Python
 * (which listens on 0.0.0.0:10000). Use the LAN address so the proxy reaches the real backend.
 * Override with CCTV_BACKEND_HOST in .env if needed.
 */
function resolveBackendHost() {
    if (process.env.CCTV_BACKEND_HOST) {
        return process.env.CCTV_BACKEND_HOST;
    }
    if (process.platform === 'win32') {
        for (const ifaces of Object.values(os.networkInterfaces())) {
            for (const iface of ifaces || []) {
                if (iface.family === 'IPv4' && !iface.internal) {
                    return iface.address;
                }
            }
        }
    }
    return '127.0.0.1';
}

const BACKEND_HOST = resolveBackendHost();
const BACKEND_PORT = Number(process.env.CCTV_BACKEND_PORT || 10000);

if (process.env.NODE_ENV !== 'production') {
    console.log(`[vite] API proxy → http://${BACKEND_HOST}:${BACKEND_PORT}`);
}

function isHttpResponse(res) {
    return Boolean(res && typeof res.writeHead === 'function' && typeof res.end === 'function');
}

function isBenignProxyError(err) {
    return Boolean(err && err.code && BENIGN_PROXY_CODES.has(err.code));
}

function proxyToBackend(req, res, next) {
    const method = (req.method || 'GET').toUpperCase();
    const hasBody = method !== 'GET' && method !== 'HEAD';

    const forward = (body) => {
        const headers = {
            ...req.headers,
            host: `${BACKEND_HOST}:${BACKEND_PORT}`,
        };
        if (body?.length) {
            headers['content-length'] = String(body.length);
        } else {
            delete headers['content-length'];
        }

        const proxyReq = http.request(
            {
                hostname: BACKEND_HOST,
                port: BACKEND_PORT,
                path: req.url,
                method,
                headers,
            },
            (proxyRes) => {
                if (isHttpResponse(res) && !res.headersSent) {
                    res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
                    proxyRes.pipe(res);
                }
            },
        );

        proxyReq.on('error', (err) => {
            if (isBenignProxyError(err)) {
                if (isHttpResponse(res) && !res.headersSent) {
                    res.writeHead(503, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({
                        error: 'Backend unavailable — start backend on port 10000',
                    }));
                }
                return;
            }
            next(err);
        });

        if (body?.length) {
            proxyReq.write(body);
        }
        proxyReq.end();
    };

    if (!hasBody) {
        forward(null);
        return;
    }

    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => forward(Buffer.concat(chunks)));
    req.on('error', (err) => next(err));
}

/**
 * Vite attaches its own proxy error loggers *after* `configure` returns.
 * Replace them on the next microtask so ECONNRESET / ECONNREFUSED don't spam the terminal.
 */
function silenceViteProxyNoise(proxy, label) {
    queueMicrotask(() => {
        proxy.removeAllListeners('error');
        proxy.on('error', (err, _req, res) => {
            if (isBenignProxyError(err)) {
                if (isHttpResponse(res) && !res.headersSent && !res.writableEnded) {
                    res.writeHead(503, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: 'Backend unavailable — retry shortly' }));
                } else if (res && typeof res.end === 'function' && !isHttpResponse(res)) {
                    try {
                        res.end();
                    } catch {
                        // socket already closed
                    }
                }
                return;
            }
            console.warn(`[vite] ${label} proxy:`, err.message || err);
            if (isHttpResponse(res) && !res.headersSent && !res.writableEnded) {
                res.writeHead(500, { 'Content-Type': 'text/plain' });
                res.end();
            }
        });

        const wsListeners = proxy.listeners('proxyReqWs').slice();
        proxy.removeAllListeners('proxyReqWs');
        for (const listener of wsListeners) {
            proxy.on('proxyReqWs', (proxyReq, req, socket, options) => {
                const origOn = socket.on.bind(socket);
                socket.on = (event, handler) => {
                    if (event === 'error') {
                        return origOn(event, (err) => {
                            if (isBenignProxyError(err)) return;
                            handler(err);
                        });
                    }
                    return origOn(event, handler);
                };
                listener(proxyReq, req, socket, options);
            });
        }
    });
}

function configureWsProxy(proxy) {
    silenceViteProxyNoise(proxy, 'ws');
}

/** Proxy /api/* before SPA fallback — all browser traffic stays on :3000. */
function apiEarlyProxyPlugin() {
    return {
        name: 'api-early-proxy',
        apply: 'serve',
        configureServer: {
            order: 'pre',
            handler(server) {
                server.middlewares.use((req, res, next) => {
                    const path = (req.url ?? '').split('?')[0];
                    if (!path.startsWith('/api') && !path.startsWith('/media')) {
                        return next();
                    }
                    proxyToBackend(req, res, next);
                });
            },
        },
    };
}

/** Proxy /go2rtc/* before Vite tries to resolve those URLs as local files. */
function go2rtcEarlyProxyPlugin() {
    return {
        name: 'go2rtc-early-proxy',
        apply: 'serve',
        configureServer: {
            order: 'pre',
            handler(server) {
                server.middlewares.use((req, res, next) => {
                    const path = (req.url ?? '').split('?')[0];
                    if (!path.startsWith('/go2rtc') || path === '/go2rtc-diagnostics') {
                        return next();
                    }
                    if ((req.headers.upgrade || '').toLowerCase() === 'websocket') {
                        return next();
                    }
                    proxyToBackend(req, res, next);
                });
            },
        },
    };
}

// https://vitejs.dev/config/
export default defineConfig({
    base: '/',
    appType: 'spa',
    plugins: [react(), apiEarlyProxyPlugin(), go2rtcEarlyProxyPlugin()],
    server: {
        host: '127.0.0.1',
        port: 3000,
        strictPort: true,
        proxy: {
            // WebSocket for go2rtc live view (and PTZ).
            '/go2rtc': {
                target: `http://${BACKEND_HOST}:${BACKEND_PORT}`,
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
