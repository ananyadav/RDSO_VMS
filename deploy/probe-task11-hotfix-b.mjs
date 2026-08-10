/**
 * Task 11 Hotfix B — trace black vs working grid tiles on production.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = process.env.LIVE_GROUP || 'rml_6_rashmi_6_paradigm_limited_precast_office';
const TARGET_IPS = ['192.168.11.27', '192.168.11.30', '192.168.11.31', '192.168.11.40'];
const WORKING_IP = process.env.WORKING_IP || '192.168.11.28';

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
    path.join(ROOT, 'deploy', 'create-test-session.py'),
  ], { cwd: ROOT, encoding: 'utf8' });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function tileState(page, ip) {
  return page.evaluate((targetIp) => {
    const tiles = [...document.querySelectorAll('.group h3')];
    const h3 = tiles.find((el) => el.textContent?.includes(targetIp));
    if (!h3) return { ip: targetIp, found: false };
    const card = h3.closest('.group');
    const tile = h3.closest('[class*="relative flex-1"]');
    const player = card?.querySelector('.live-monitor-player');
    const vs = card?.querySelector('video-stream');
    const video = card?.querySelector('video');
    const overlay = card?.querySelector('.animate-pulse');
    const grid = document.querySelector('[data-live-grid-cols]');
    const row = tile?.closest('[class*="absolute left-0"]');
    return {
      ip: targetIp,
      found: true,
      hasPlayerDiv: Boolean(player),
      hasVideoStream: Boolean(vs),
      hasVideo: Boolean(video),
      videoWidth: video?.videoWidth ?? 0,
      videoHeight: video?.videoHeight ?? 0,
      videoPaused: video?.paused ?? null,
      videoReadyState: video?.readyState ?? null,
      modeLabel: vs?.querySelector?.('.mode')?.textContent?.trim() ?? null,
      statusLabel: vs?.querySelector?.('.status')?.textContent?.trim() ?? null,
      streamSrc: vs?.src ?? null,
      overlayVisible: Boolean(overlay),
      overlayText: overlay?.textContent?.trim() ?? null,
      gridMounted: grid?.getAttribute('data-live-grid-mounted'),
      gridEligible: grid?.getAttribute('data-live-grid-stream-eligible'),
    };
  }, ip);
}

async function main() {
  const cookie = sessionCookie();
  const out = { base: BASE, group: GROUP, at: new Date().toISOString(), wsEvents: [], tiles: {}, tests: {} };

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  await context.addCookies([
    { name: cookie.name, value: cookie.value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
  const page = await context.newPage();
  page.on('dialog', (d) => d.accept());

  const wsLog = [];
  page.on('websocket', (ws) => {
    const entry = { url: ws.url(), open: true, closeCode: null, closeReason: null, errors: [] };
    wsLog.push(entry);
    ws.on('close', (code, reason) => {
      entry.open = false;
      entry.closeCode = code;
      entry.closeReason = reason;
    });
    ws.on('socketerror', (err) => entry.errors.push(String(err)));
  });

  page.on('console', (msg) => {
    const t = msg.text();
    if (t.includes('[live-media]') || t.includes('[grid-debug]')) out.console = [...(out.console || []), t.slice(0, 300)];
  });

  await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  await page.waitForTimeout(22000);

  for (const ip of [...TARGET_IPS, WORKING_IP]) {
    out.tiles[ip] = await tileState(page, ip);
    const src = out.tiles[ip].streamSrc || '';
    const uidGuess = src.match(/src=([^&]+)/)?.[1] || null;
    out.tiles[ip].streamId = uidGuess;
    out.tiles[ip].profile = uidGuess?.endsWith('_sub') ? 'sub/ch102' : uidGuess?.endsWith('_main') ? 'main/ch101' : null;
    out.tiles[ip].worker = src.match(/\/media\/(w\d+)\//)?.[1] ?? null;
    const relatedWs = wsLog.filter((w) => uidGuess && w.url.includes(uidGuess));
    out.tiles[ip].ws = relatedWs.map((w) => ({ url: w.url, open: w.open, closeCode: w.closeCode, errors: w.errors }));
  }

  // Classify black targets
  for (const ip of TARGET_IPS) {
    const t = out.tiles[ip];
    if (!t.found) t.classification = 'not-in-viewport';
    else if (!t.hasVideoStream && !t.overlayVisible) t.classification = 'A-no-ws-element-no-overlay';
    else if (t.hasVideoStream && t.videoWidth === 0 && t.overlayVisible) t.classification = 'C-connecting';
    else if (t.hasVideoStream && t.videoWidth === 0 && !t.overlayVisible) t.classification = 'C-no-frame-no-overlay-BLACK';
    else if (t.hasVideoStream && t.videoWidth > 0) t.classification = 'OK-playing';
    else t.classification = 'unknown';
  }

  // Manual reconnect test on .27 while visible
  const ip = TARGET_IPS[0];
  const reconnect = await page.evaluate(async (targetIp) => {
    const h3 = [...document.querySelectorAll('.group h3')].find((el) => el.textContent?.includes(targetIp));
    const card = h3?.closest('.group');
    const vs = card?.querySelector('video-stream');
    const before = {
      src: vs?.src ?? null,
      videoWidth: card?.querySelector('video')?.videoWidth ?? 0,
      mode: vs?.querySelector?.('.mode')?.textContent?.trim() ?? null,
    };
    if (!vs || typeof vs.ondisconnect !== 'function') return { before, error: 'no video-stream or ondisconnect' };
    try {
      vs.ondisconnect();
      vs.src = before.src;
    } catch (e) {
      return { before, error: String(e) };
    }
    await new Promise((r) => setTimeout(r, 5000));
    const video = card?.querySelector('video');
    return {
      before,
      after: {
        videoWidth: video?.videoWidth ?? 0,
        mode: card?.querySelector('video-stream')?.querySelector?.('.mode')?.textContent?.trim() ?? null,
      },
    };
  }, ip);
  out.tests.manualReconnect27 = reconnect;

  // Fullscreen on .27 — capture stream profile
  const targetTile = page.locator('.group h3', { hasText: ip }).first();
  if (await targetTile.count()) {
    await targetTile.dblclick();
    await page.waitForTimeout(12000);
    out.tests.fullscreen27 = await page.evaluate(() => {
      const vs = document.querySelector('.fixed video-stream') || document.querySelector('video-stream');
      const src = vs?.src ?? null;
      const uid = src?.match(/src=([^&]+)/)?.[1] ?? null;
      const video = document.querySelector('.fixed video') || document.querySelector('video');
      return {
        streamSrc: src,
        streamId: uid,
        profile: uid?.endsWith('_sub') ? 'sub/ch102' : uid?.endsWith('_main') ? 'main/ch101' : null,
        worker: src?.match(/\/media\/(w\d+)\//)?.[1] ?? null,
        videoWidth: video?.videoWidth ?? 0,
        statusText: document.body.innerText.match(/Playing · .* · go2rtc|Connecting · .* · go2rtc/)?.[0] ?? null,
      };
    });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(3000);
    out.tests.afterFullscreenGrid27 = await tileState(page, ip);
  }

  out.wsEvents = wsLog.slice(0, 80).map((w) => ({ url: w.url, open: w.open, closeCode: w.closeCode }));
  fs.writeFileSync(path.join(ROOT, 'deploy', 'task11-hotfix-b-trace.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
