/**
 * Task 13 — 6x6 + Control Room UI validation on production.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = 'rml_6_isp_labour_housing_2';
const HOLD_MS = Number(process.env.TASK13_HOLD_MS || 6 * 60 * 1000);

const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
  path.join(ROOT, 'deploy', 'create-test-session.py'),
], { cwd: ROOT, encoding: 'utf8' });
const [name, value] = py.stdout.trim().split('\n').pop().split('=');

function srcOf(url) {
  try {
    return new URL(url).searchParams.get('src');
  } catch {
    return url;
  }
}

const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'],
});
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
await ctx.addCookies([{ name, value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await ctx.newPage();
page.setDefaultTimeout(120000);

const ws = [];
const open = new Map();
let maxConcurrent = 0;
let dups = 0;
let legacy = 0;
page.on('websocket', (w) => {
  const url = w.url();
  const src = srcOf(url);
  ws.push(url);
  if (url.includes('/go2rtc/api/ws')) legacy += 1;
  const n = (open.get(src) || 0) + 1;
  open.set(src, n);
  if (n > 1 && url.includes('/media/')) dups += 1;
  const c = [...open.values()].reduce((a, b) => a + b, 0);
  if (c > maxConcurrent) maxConcurrent = c;
  w.on('close', () => {
    const left = (open.get(src) || 1) - 1;
    if (left <= 0) open.delete(src);
    else open.set(src, left);
  });
});

async function snap() {
  return page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    const sidebar = document.querySelector('nav');
    return {
      href: location.href,
      controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
      cols: el?.getAttribute('data-live-grid-cols'),
      total: el?.getAttribute('data-live-grid-total'),
      mounted: el?.getAttribute('data-live-grid-mounted'),
      eligible: el?.getAttribute('data-live-grid-stream-eligible'),
      playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      streams: document.querySelectorAll('video-stream').length,
      sidebarVisible: Boolean(sidebar && sidebar.offsetParent !== null),
      hasExit: [...document.querySelectorAll('button')].some((b) => /exit/i.test(b.textContent || '')),
      hasControlRoomBtn: [...document.querySelectorAll('button')].some((b) => /control room/i.test(b.textContent || '')),
    };
  });
}

async function pickLayout(label) {
  await page.getByTitle('Grid layout').selectOption(label);
}

await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForSelector('[data-live-grid-cols]');
await page.waitForTimeout(16000);
const five = await snap();
const fiveOpenWs = [...open.values()].reduce((a, b) => a + b, 0);

let six, sixOpenWs, sixDups, sixScrolled, sixBack, cr, fsOpen, afterFsEsc, afterCrEsc, restored, ptz;
let holdSamples = [];
let mem0 = null;
let mem1 = null;
try {
  await pickLayout('6x6');
  await page.waitForTimeout(14000);
  six = await snap();
  sixOpenWs = [...open.values()].reduce((a, b) => a + b, 0);
  sixDups = dups;

  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = Math.min(el.scrollHeight, 900);
  });
  await page.waitForTimeout(4000);
  sixScrolled = await snap();
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 0;
  });
  await page.waitForTimeout(6000);
  sixBack = await snap();

  await page.getByRole('button', { name: /control room/i }).click();
  await page.waitForTimeout(4000);
  cr = await snap();

  const tile = page.locator('[data-live-grid-cols] .group h3').first();
  await tile.dblclick();
  await page.waitForTimeout(3500);
  fsOpen = await page.evaluate(() => ({
    status: (document.body.innerText.match(/Playing · .* · go2rtc|Connecting · .* · go2rtc/) || [])[0] || null,
    controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
  }));
  await page.keyboard.press('Escape');
  await page.waitForTimeout(2000);
  afterFsEsc = await snap();
  await page.keyboard.press('Escape');
  await page.waitForTimeout(2000);
  afterCrEsc = await snap();

  await page.getByRole('button', { name: /control room/i }).click();
  await page.waitForTimeout(2000);
  await pickLayout('6x6').catch(() => {});
  mem0 = await page.evaluate(() => {
    const m = performance.memory;
    return m ? Math.round(m.usedJSHeapSize / 1048576) : null;
  });
  const holdStarted = Date.now();
  while (Date.now() - holdStarted < HOLD_MS) {
    await page.waitForTimeout(30000);
    holdSamples.push({
      t: new Date().toISOString(),
      ...(await snap()),
      openWs: [...open.values()].reduce((a, b) => a + b, 0),
      heap: await page.evaluate(() => {
        const m = performance.memory;
        return m ? Math.round(m.usedJSHeapSize / 1048576) : null;
      }),
    });
  }
  mem1 = await page.evaluate(() => {
    const m = performance.memory;
    return m ? Math.round(m.usedJSHeapSize / 1048576) : null;
  });

  await page.getByRole('button', { name: /^exit$/i }).click().catch(() => {});
  await page.waitForTimeout(2000);
  restored = await snap();

  await page.goto(`${BASE}/ptz`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(4000);
  ptz = await page.evaluate(() => ({
    href: location.href,
    crashed: Boolean(document.body.innerText.match(/Something went wrong|ErrorBoundary/i)),
    heading: (document.body.innerText.match(/PTZ[^\n]{0,80}/) || [])[0] || null,
  }));
} catch (err) {
  fs.writeFileSync(
    path.join(ROOT, 'deploy', 'task13-prod-error.json'),
    JSON.stringify({ error: String(err), five, six, sixScrolled, sixBack, cr, fsOpen, afterFsEsc, afterCrEsc, restored }, null, 2),
  );
  throw err;
}

const cookie = `${name}=${value}`;
const health = await (await fetch(`${BASE}/api/health`, { headers: { Cookie: cookie } })).json();
const status = await (await fetch(`${BASE}/api/go2rtc/status`, { headers: { Cookie: cookie } })).json();

const workersHit = [...new Set(ws.filter((u) => /\/media\/w\d+\//.test(u)).map((u) => (u.match(/\/media\/(w\d+)\//) || [])[1]))];
const out = {
  five,
  fiveOpenWs,
  six,
  sixScrolled,
  sixBack,
  cr,
  fsOpen,
  afterFsEsc,
  afterCrEsc,
  restored,
  ptz,
  sixOpenWs,
  sixDups,
  holdSamples,
  mem0,
  mem1,
  ws: {
    opened: ws.length,
    legacy,
    dups,
    maxConcurrent,
    workersHit,
    openAtEnd: [...open.values()].reduce((a, b) => a + b, 0),
  },
  health,
  workers: (status.workers || []).map((w) => ({ id: w.workerId, running: w.running, streams: w.liveStreamCount })),
};
fs.writeFileSync(path.join(ROOT, 'deploy', 'task13-prod.json'), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
await browser.close();
