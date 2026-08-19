import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://192.168.17.150';
const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
  path.join(ROOT, 'deploy', 'create-test-session.py'),
], { cwd: ROOT, encoding: 'utf8' });
const [name, value] = py.stdout.trim().split('\n').pop().split('=');
const cookie = `${name}=${value}`;

async function api(p, opts = {}) {
  const t0 = Date.now();
  const res = await fetch(`${BASE}${p}`, { ...opts, headers: { Cookie: cookie, ...(opts.headers || {}) } });
  const body = await res.json().catch(async () => ({ text: await res.text() }));
  return { status: res.status, ms: Date.now() - t0, body };
}

const id = '6a38e9f9c8082fc30758240d';
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addCookies([{ name, value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await ctx.newPage();
await page.goto(`${BASE}/live?group=${encodeURIComponent('rml_6_isp_labour_housing_2')}&layout=5x5`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(12000);
const grid = await page.evaluate(() => ({
  playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
  eligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
}));
const st = await api(`/api/ptz/${id}/status`);
const up = await api(`/api/ptz/${id}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: 'up', speed: 2 }) });
await page.waitForTimeout(400);
const stop1 = await api(`/api/ptz/${id}/stop`, { method: 'POST' });
const left = await api(`/api/ptz/${id}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: 'left', speed: 2 }) });
await page.waitForTimeout(400);
await api(`/api/ptz/${id}/stop`, { method: 'POST' });
const zin = await api(`/api/ptz/${id}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ direction: 'zoom_in', speed: 2 }) });
await page.waitForTimeout(300);
await api(`/api/ptz/${id}/stop`, { method: 'POST' });
const health = await api('/api/health');
const gridAfter = await page.evaluate(() => ({
  playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
}));
console.log(JSON.stringify({ grid, gridAfter, st, up, stop1, left, zin, health }, null, 2));
await browser.close();
