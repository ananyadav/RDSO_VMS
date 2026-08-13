/**
 * Task 12A — post-deploy verification from LAN client.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = 'rml_6_rashmi_6_paradigm_limited_precast_office';

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
    path.join(ROOT, 'deploy', 'create-test-session.py'),
  ], { cwd: ROOT, encoding: 'utf8' });
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function main() {
  const cookie = sessionCookie();
  const out = { base: BASE, at: new Date().toISOString(), tests: {} };

  // External :10000 must fail
  try {
    const ctrl = AbortSignal.timeout(3000);
    const r = await fetch(`http://192.168.17.150:10000/api/health`, { signal: ctrl });
    out.tests.external10000 = { reachable: true, status: r.status, fail: true };
  } catch (e) {
    out.tests.external10000 = { reachable: false, error: e.cause?.code || e.name, pass: true };
  }

  // Nginx API
  const health = await fetch(`${BASE}/api/health`);
  out.tests.nginxHealth = { status: health.status, body: await health.json() };

  // Auth
  out.tests.mediaLogout = { status: (await fetch(`${BASE}/media/w1/api`)).status };
  out.tests.go2rtcWsLogout = { status: (await fetch(`${BASE}/go2rtc/api/ws?src=x`)).status };
  const authed = await fetch(`${BASE}/go2rtc/api/ws?src=x`, {
    headers: { Cookie: `${cookie.name}=${cookie.value}` },
  });
  out.tests.go2rtcWsAuthed = { status: authed.status, text: (await authed.text()).slice(0, 120) };
  const badAuth = await fetch(`${BASE}/api/go2rtc/media-auth?src=nope`, {
    headers: { Cookie: `${cookie.name}=${cookie.value}` },
  });
  out.tests.badStreamAuth = { status: badAuth.status };

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  await ctx.addCookies([
    { name: cookie.name, value: cookie.value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
  const page = await ctx.newPage();
  const ws = [];
  page.on('websocket', (w) => ws.push(w.url()));

  // Worker samples via API
  const status = await (await fetch(`${BASE}/api/go2rtc/status`, {
    headers: { Cookie: `${cookie.name}=${cookie.value}` },
  })).json();
  out.tests.workers = (status.workers || []).map((w) => ({
    id: w.workerId,
    port: w.apiPort,
    running: w.running,
  }));

  await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(18000);

  const grid = await page.evaluate(() => ({
    mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
    eligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
    playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
    streams: document.querySelectorAll('video-stream').length,
  }));
  out.tests.grid5x5 = grid;

  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 1200;
  });
  await page.waitForTimeout(4000);
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 0;
  });
  await page.waitForTimeout(5000);
  out.tests.afterScroll = await page.evaluate(() => ({
    playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
  }));

  const tile = page.locator('.group h3').first();
  if (await tile.count()) {
    await tile.dblclick();
    await page.waitForTimeout(3000);
    out.tests.fullscreen = { playing: (await page.evaluate(() => document.querySelector('video')?.videoWidth ?? 0)) };
    await page.keyboard.press('Escape');
    await page.waitForTimeout(2000);
  }

  const mediaWs = ws.filter((u) => /\/media\/w\d+\/api\/ws/.test(u));
  out.tests.media = {
    workersHit: [...new Set(mediaWs.map((u) => (u.match(/\/media\/(w\d+)\//) || [])[1]))],
    legacy: ws.filter((u) => u.includes('/go2rtc/api/ws')).length,
    sample: mediaWs.slice(0, 6),
  };

  fs.writeFileSync(path.join(ROOT, 'deploy', 'task12a-prod-verify.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
