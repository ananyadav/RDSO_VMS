/**
 * Task 13.1 validation — Control Room overscan must not stream.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP = 'rml_6_isp_labour_housing_2';
const SETTLE = Number(process.env.TASK131_SETTLE_MS || 18000);
const SOAK_MS = Number(process.env.TASK131_SOAK_MS || 5 * 60 * 1000);
const USER = process.env.TASK131_USER || 'admin123';
const PASS = process.env.TASK131_PASS;
if (!PASS) {
  console.error('TASK131_PASS is required');
  process.exit(1);
}

function track() {
  const open = new Map();
  let legacy = 0;
  let opened = 0;
  return {
    bind(page) {
      page.on('websocket', (w) => {
        const url = w.url();
        opened += 1;
        if (url.includes('/go2rtc/api/ws')) legacy += 1;
        if (!url.includes('/media/')) return;
        open.set(url, (open.get(url) || 0) + 1);
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
    opened() {
      return opened;
    },
  };
}

async function snap(page, sock) {
  const grid = await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    const cards = [...document.querySelectorAll('[data-live-stream-eligible]')];
    const eligibleCards = cards.filter((c) => c.getAttribute('data-live-stream-eligible') === 'true');
    const ineligible = cards.filter((c) => c.getAttribute('data-live-stream-eligible') === 'false');
    const activeOf = (list) =>
      list.filter((c) => {
        const st = c.getAttribute('data-live-stream-status') || '';
        const q = c.getAttribute('data-live-stream-queued') === 'true';
        return q || st === 'connecting' || st === 'playing' || st === 'error';
      }).length;
    return {
      href: location.href,
      controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
      cols: el?.getAttribute('data-live-grid-cols'),
      mounted: Number(el?.getAttribute('data-live-grid-mounted') || 0),
      eligible: Number(el?.getAttribute('data-live-grid-stream-eligible') || 0),
      playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      streams: document.querySelectorAll('video-stream').length,
      fullscreen: Boolean(document.fullscreenElement),
      sidebar: Boolean(document.querySelector('nav') && document.querySelector('nav').offsetParent),
      cards: cards.length,
      eligibleCards: eligibleCards.length,
      ineligibleCards: ineligible.length,
      eligibleActive: activeOf(eligibleCards),
      ineligibleActive: activeOf(ineligible),
      ineligibleStatuses: ineligible.map((c) => c.getAttribute('data-live-stream-status')),
    };
  });
  return { ...grid, ws: sock.ws(), legacy: sock.legacy() };
}

const browser = await chromium.launch({
  channel: 'msedge',
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required'],
});
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();
page.setDefaultTimeout(120000);
const sock = track();
sock.bind(page);
const out = { bundle: null, five: {}, six: {}, transitions: {}, soak: null, fullscreenCam: null };

try {
  await page.goto(`${BASE}/live`, { waitUntil: 'domcontentloaded', timeout: 120000 });
  await page.locator('#username').waitFor({ timeout: 30000 });
  await page.fill('#username', USER);
  await page.fill('#password', PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForFunction(() => !document.querySelector('#username'), null, { timeout: 30000 });
  out.bundle = await page.evaluate(() => [...document.querySelectorAll('script')].map((s) => s.src).find((s) => s.includes('index-')) || document.documentElement.innerHTML.match(/index-[^"]+\.js/)?.[0]);

  await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-live-grid-cols]');
  await page.waitForTimeout(SETTLE);
  out.five.normal = await snap(page, sock);

  await page.getByLabel('Fullscreen video wall').click();
  await page.waitForTimeout(SETTLE);
  out.five.controlRoom = await snap(page, sock);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);
  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await page.waitForTimeout(2500);

  await page.getByTitle('Grid layout').selectOption('6x6');
  await page.waitForTimeout(SETTLE);
  out.six.normal = await snap(page, sock);

  await page.getByLabel('Fullscreen video wall').click();
  await page.waitForTimeout(SETTLE);
  out.six.controlRoom = await snap(page, sock);

  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = Math.min(el.scrollHeight, 900);
  });
  await page.waitForTimeout(SETTLE);
  out.six.crScrolled = await snap(page, sock);
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 0;
  });
  await page.waitForTimeout(SETTLE);
  out.six.crBack = await snap(page, sock);

  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);
  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await page.waitForTimeout(SETTLE);
  out.transitions.afterEsc = await snap(page, sock);

  await page.getByLabel('Fullscreen video wall').click();
  await page.waitForTimeout(SETTLE);
  out.transitions.reenter = await snap(page, sock);

  const soakSamples = [];
  const soakStart = Date.now();
  while (Date.now() - soakStart < SOAK_MS) {
    await page.waitForTimeout(30000);
    soakSamples.push({ t: Date.now() - soakStart, ...(await snap(page, sock)), opened: sock.opened() });
  }
  out.soak = { samples: soakSamples };

  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);
  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await page.waitForTimeout(2500);

  const tile = page.locator('[data-live-grid-cols] h3').first();
  await tile.dblclick({ timeout: 15000 });
  await page.waitForTimeout(2500);
  out.fullscreenCam = await page.evaluate(() => ({
    playingHint: (document.body.innerText.match(/Playing ·|Connecting ·/) || [])[0] || null,
    controlRoom: document.querySelector('[data-live-control-room]')?.getAttribute('data-live-control-room'),
    modal: Boolean(document.querySelector('[role="dialog"], .fixed')),
  }));
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1500);
  out.fullscreenCam.afterEsc = await snap(page, sock);
} finally {
  const cr6 = out.six.controlRoom || {};
  out.pass =
    cr6.mounted >= 42 &&
    cr6.eligible === 36 &&
    (cr6.ineligibleActive || 0) === 0 &&
    (cr6.playing <= 38) &&
    (cr6.ws === undefined || cr6.ws <= 38) &&
    (cr6.ws === undefined || cr6.ws < 48);
  fs.writeFileSync(path.join(ROOT, 'deploy', 'task13.1-validate.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close().catch(() => {});
}
