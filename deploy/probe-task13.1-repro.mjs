/**
 * Task 13.1 — reproduce Control Room overscan streaming (read-only).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = 'rml_6_isp_labour_housing_2';
const SETTLE = Number(process.env.TASK131_SETTLE_MS || 18000);
const ADMIN_USER = process.env.TASK131_USER || 'admin123';
const ADMIN_PASS = process.env.TASK131_PASS;
if (!ADMIN_PASS) {
  console.error('TASK131_PASS is required');
  process.exit(1);
}

function track() {
  const open = new Map();
  const opened = [];
  let legacy = 0;
  return {
    bind(page) {
      page.on('websocket', (w) => {
        const url = w.url();
        opened.push(url);
        if (url.includes('/go2rtc/api/ws')) legacy += 1;
        if (!url.includes('/media/')) return;
        const n = (open.get(url) || 0) + 1;
        open.set(url, n);
        w.on('close', () => {
          const left = (open.get(url) || 1) - 1;
          if (left <= 0) open.delete(url);
          else open.set(url, left);
        });
      });
    },
    ws() {
      return [...open.values()].reduce((a, b) => a + b, 0);
    },
    legacy() {
      return legacy;
    },
  };
}

async function snap(page, ws) {
  const grid = await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    return {
      href: location.href,
      controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
      cols: el?.getAttribute('data-live-grid-cols'),
      total: el?.getAttribute('data-live-grid-total'),
      mounted: Number(el?.getAttribute('data-live-grid-mounted') || 0),
      eligible: Number(el?.getAttribute('data-live-grid-stream-eligible') || 0),
      playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      streams: document.querySelectorAll('video-stream').length,
      fullscreen: Boolean(document.fullscreenElement),
    };
  });
  return { ...grid, ws: ws.ws(), legacy: ws.legacy() };
}

const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'],
});
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();
page.setDefaultTimeout(120000);
const ws = track();
ws.bind(page);

await page.goto(`${BASE}/live`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.locator('#username').waitFor({ timeout: 30000 });
await page.fill('#username', ADMIN_USER);
await page.fill('#password', ADMIN_PASS);
await page.getByRole('button', { name: /sign in/i }).click();
await page.waitForFunction(() => !document.querySelector('#username'), null, { timeout: 30000 });
await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=6x6`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForSelector('[data-live-grid-cols]');
await page.waitForTimeout(SETTLE);
const before = await snap(page, ws);
console.log('BEFORE_NORMAL_6x6', JSON.stringify(before));

await page.getByLabel('Fullscreen video wall').click();
await page.waitForTimeout(SETTLE);
const cr = await snap(page, ws);
console.log('CONTROL_ROOM_6x6', JSON.stringify(cr));

const reproduced =
  before.eligible === 36 &&
  before.playing <= 36 &&
  cr.eligible === 36 &&
  (cr.playing >= 48 || cr.ws >= 48 || cr.streams >= 48);
console.log('REPRODUCED', reproduced);

fs.writeFileSync(
  path.join(ROOT, 'deploy', 'task13.1-repro.json'),
  JSON.stringify({ before, cr, reproduced }, null, 2),
);
await browser.close();
