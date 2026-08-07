/**
 * Task 11 — production smoke probes against http://192.168.17.150
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function main() {
  const cookie = sessionCookie();
  const out = { base: BASE, cookieName: cookie.name, tests: {} };

  // HTTP checks
  for (const [key, url, wantAuth] of [
    ['health', `${BASE}/api/health`, false],
    ['home', `${BASE}/`, false],
    ['media_logged_out', `${BASE}/media/w1/api/streams`, true],
    ['go2rtc_ws_logged_out', `${BASE}/go2rtc/api/ws?src=test`, true],
  ]) {
    try {
      const res = await fetch(url, { redirect: 'manual' });
      out.tests[key] = { status: res.status, ok: res.ok };
      if (key === 'home') {
        const text = await res.text();
        out.tests[key].spa =
          text.includes('assets/') && !text.includes('@vite/client')
            ? 'production'
            : text.includes('@vite/client')
              ? 'vite-dev'
              : 'unknown';
      }
      if (key === 'health') out.tests[key].body = await res.json();
    } catch (e) {
      out.tests[key] = { error: String(e) };
    }
  }

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext();
  await context.addCookies([
    {
      name: cookie.name,
      value: cookie.value,
      url: BASE,
      httpOnly: true,
      sameSite: 'Lax',
    },
  ]);
  const page = await context.newPage();
  page.on('dialog', (d) => d.accept());

  const wsUrls = [];
  const mediaLogs = [];
  page.on('websocket', (ws) => {
    wsUrls.push(ws.url());
  });
  page.on('console', (msg) => {
    const t = msg.text();
    if (t.includes('[live-media]')) mediaLogs.push(t.slice(0, 300));
  });

  await page.goto(`${BASE}/live`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.waitForTimeout(4000);

  // Try select first site / 5x5 if controls exist
  const siteSelect = page.locator('select').nth(0);
  if (await siteSelect.count()) {
    const options = await siteSelect.locator('option').allTextContents();
    const idx = options.findIndex((t) => /RML/i.test(t));
    if (idx >= 0) await siteSelect.selectOption({ index: idx });
  }
  const layoutBtn = page.locator('button', { hasText: '5x5' }).first();
  if (await layoutBtn.count()) await layoutBtn.click();
  await page.waitForTimeout(20000);

  out.tests.grid = await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    return {
      cols: el?.getAttribute('data-live-grid-cols'),
      total: el?.getAttribute('data-live-grid-total'),
      mounted: el?.getAttribute('data-live-grid-mounted'),
      streamEligible: el?.getAttribute('data-live-grid-stream-eligible'),
      videoStreams: document.querySelectorAll('video-stream').length,
      videos: document.querySelectorAll('video').length,
      metrics: window.__nvrLiveMetrics?.summary?.() ?? null,
      completeN: window.__nvrLiveMetrics?.getAll?.().complete?.length ?? 0,
      cancelledN: window.__nvrLiveMetrics?.getAll?.().cancelled?.length ?? 0,
    };
  });

  out.tests.websockets = {
    all: wsUrls.slice(0, 40),
    mediaPaths: wsUrls.filter((u) => u.includes('/media/w')).slice(0, 20),
    legacyGo2rtcWs: wsUrls.filter((u) => u.includes('/go2rtc/api/ws')),
  };
  out.tests.mediaLogs = mediaLogs.slice(0, 10);
  out.tests.directMedia =
    out.tests.websockets.mediaPaths.length > 0 &&
    out.tests.websockets.legacyGo2rtcWs.length === 0;

  // Security with cookie: go2rtc/api/ws should be 410 when authenticated
  const go2 = await page.evaluate(async () => {
    const r = await fetch('/go2rtc/api/ws?src=test');
    return { status: r.status, text: (await r.text()).slice(0, 120) };
  });
  out.tests.go2rtc_ws_authed = go2;

  fs.writeFileSync(path.join(ROOT, 'deploy', 'task11-prod-smoke.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
