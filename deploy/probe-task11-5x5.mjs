/**
 * Task 11 — large-group 5x5 overscan + scroll cancel smoke.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = process.env.LIVE_GROUP || 'rml_6_isp_labour_housing_2';

const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
  cwd: ROOT,
  encoding: 'utf8',
});
const [name, value] = py.stdout.trim().split('\n').pop().split('=');

const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'],
});
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await context.addCookies([{ name, value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await context.newPage();
const ws = [];
page.on('websocket', (w) => ws.push({ url: w.url(), t: Date.now() }));

await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(18000);

const settle = await page.evaluate(() => {
  const el = document.querySelector('[data-live-grid-cols]');
  return {
    cols: el?.getAttribute('data-live-grid-cols'),
    total: el?.getAttribute('data-live-grid-total'),
    mounted: el?.getAttribute('data-live-grid-mounted'),
    streamEligible: el?.getAttribute('data-live-grid-stream-eligible'),
    videoStreams: document.querySelectorAll('video-stream').length,
    videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
    metricsGlobal: typeof window.__nvrLiveMetrics,
  };
});

const beforeScrollWs = ws.filter((x) => /\/media\/w\d+\/api\/ws/.test(x.url)).length;
await page.evaluate(() => {
  const el = document.querySelector('[data-live-grid-cols]');
  if (el) el.scrollTop = 1800;
});
await page.waitForTimeout(5000);
const mid = await page.evaluate(() => ({
  mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
  streamEligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
  videoStreams: document.querySelectorAll('video-stream').length,
  videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
}));
await page.evaluate(() => {
  const el = document.querySelector('[data-live-grid-cols]');
  if (el) el.scrollTop = 0;
});
await page.waitForTimeout(8000);
const back = await page.evaluate(() => ({
  mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
  streamEligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
  videoStreams: document.querySelectorAll('video-stream').length,
  videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
}));

// media-auth probes
const auth = await page.evaluate(async () => {
  const bad = await fetch('/api/go2rtc/media-auth?src=does_not_exist_stream');
  const empty = await fetch('/api/go2rtc/media-auth');
  return {
    badSrc: { status: bad.status, text: (await bad.text()).slice(0, 120) },
    empty: { status: empty.status, text: (await empty.text()).slice(0, 120) },
  };
});

// PTZ page load
await page.goto(`${BASE}/ptz`, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(2500);
const ptz = await page.evaluate(() => ({
  href: location.href,
  snippet: document.body.innerText.slice(0, 300),
  hasControls: /PTZ|Pan|Tilt|Zoom/i.test(document.body.innerText),
}));

const mediaWs = ws.map((x) => x.url).filter((u) => /\/media\/w\d+\/api\/ws/.test(u));
const out = {
  group: GROUP,
  settle,
  mid,
  back,
  beforeScrollWs,
  mediaWsCount: mediaWs.length,
  workersHit: [...new Set(mediaWs.map((u) => (u.match(/\/media\/(w\d+)\//) || [])[1]))],
  legacy: ws.filter((x) => x.url.includes('/go2rtc/api/ws')).length,
  overscanGap: Number(settle.mounted || 0) - Number(settle.streamEligible || 0),
  auth,
  ptz,
};
fs.writeFileSync(path.join(ROOT, 'deploy', 'task11-prod-5x5.json'), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
await browser.close();
