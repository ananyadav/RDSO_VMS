/**
 * Task 13 hotfix — true browser fullscreen Control Room checks.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = 'rml_6_isp_labour_housing_2';

const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
  path.join(ROOT, 'deploy', 'create-test-session.py'),
], { cwd: ROOT, encoding: 'utf8' });
const [name, value] = py.stdout.trim().split('\n').pop().split('=');

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
let dups = 0;
let legacy = 0;
page.on('websocket', (w) => {
  const url = w.url();
  ws.push(url);
  if (url.includes('/go2rtc/api/ws')) legacy += 1;
  let src = url;
  try { src = new URL(url).searchParams.get('src') || url; } catch { /* ignore */ }
  const n = (open.get(src) || 0) + 1;
  open.set(src, n);
  if (n > 1 && url.includes('/media/')) dups += 1;
  w.on('close', () => {
    const left = (open.get(src) || 1) - 1;
    if (left <= 0) open.delete(src);
    else open.set(src, left);
  });
});

async function snap() {
  return page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    const gridText = el ? el.innerText : '';
    const sidebar = document.querySelector('nav');
    const fs = document.fullscreenElement;
    return {
      controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
      cols: el?.getAttribute('data-live-grid-cols'),
      mounted: el?.getAttribute('data-live-grid-mounted'),
      eligible: el?.getAttribute('data-live-grid-stream-eligible'),
      playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      streams: document.querySelectorAll('video-stream').length,
      sidebarVisible: Boolean(sidebar && sidebar.offsetParent !== null),
      hasExit: [...document.querySelectorAll('button')].some((b) => /^exit$/i.test((b.textContent || '').trim())),
      hasControlRoomBtn: [...document.querySelectorAll('button')].some((b) => /control room/i.test(b.textContent || '')),
      hasLayoutSelect: Boolean(document.querySelector('select[title="Grid layout"]')),
      hasOnlineBadge: /Online|Offline/.test(gridText),
      hasFullscreenHint: /Double-click for fullscreen/.test(gridText),
      hasRecord: /\bRecord\b|\bStop\b/.test(gridText),
      fullscreenElement: fs ? (fs.getAttribute('data-live-control-room-wall') || fs.tagName) : null,
      fullscreenTag: fs ? fs.tagName : null,
    };
  });
}

const snaps = {};
async function take(key) {
  snaps[key] = await snap();
  console.log(key, JSON.stringify(snaps[key]));
}

try {
await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForSelector('[data-live-grid-cols]');
await page.waitForTimeout(12000);
await take('fiveNormal');

await page.getByTitle('Grid layout').selectOption('6x6');
await page.waitForTimeout(12000);
await take('sixNormal');

await page.getByRole('button', { name: /control room/i }).click();
await page.waitForTimeout(4000);
await take('sixFs');

await page.evaluate(() => {
  const el = document.querySelector('[data-live-grid-cols]');
  if (el) el.scrollTop = Math.min(el.scrollHeight, 900);
});
await page.waitForTimeout(5000);
await take('sixFsScrolled');
await page.evaluate(() => {
  const el = document.querySelector('[data-live-grid-cols]');
  if (el) el.scrollTop = 0;
});
await page.waitForTimeout(5000);
await take('sixFsBack');

await page.keyboard.press('Escape');
await page.waitForTimeout(1500);
await page.evaluate(async () => {
  if (document.fullscreenElement && document.exitFullscreen) {
    await document.exitFullscreen();
  }
});
await page.waitForTimeout(2500);
await take('afterEsc');

const tile = page.locator('[data-live-grid-cols] h3').first();
await tile.dblclick({ timeout: 15000 });
await page.waitForTimeout(2500);
snaps.afterDbl = await page.evaluate(() => ({
  status: (document.body.innerText.match(/Playing · .* · go2rtc|Connecting · .* · go2rtc/) || [])[0] || null,
  controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
}));
console.log('afterDbl', JSON.stringify(snaps.afterDbl));
await page.keyboard.press('Escape');
await page.waitForTimeout(1500);

await page.getByTitle('Grid layout').selectOption('5x5');
await page.waitForTimeout(8000);
await page.getByRole('button', { name: /control room/i }).click();
await page.waitForTimeout(4000);
await take('fiveFs');
await page.keyboard.press('Escape');
await page.waitForTimeout(800);
await page.evaluate(async () => {
  if (document.fullscreenElement && document.exitFullscreen) {
    await document.exitFullscreen();
  }
});
await page.waitForTimeout(2000);
await take('fiveRestored');
} finally {
const out = {
  ...snaps,
  ws: {
    opened: ws.length,
    legacy,
    dups,
    openAtEnd: [...open.values()].reduce((a, b) => a + b, 0),
  },
};
fs.writeFileSync(path.join(ROOT, 'deploy', 'task13-hotfix-fs.json'), JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
await browser.close().catch(() => {});
}
