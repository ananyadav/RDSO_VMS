/**
 * Quick probe one Task 9 camera fullscreen.
 * Usage: node deploy/probe-task9-one.mjs <ip-suffix-or-id>
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://127.0.0.1:8080';
const GROUP = 'rml_6_corporate_office_2nd_floor';
const TARGETS = {
  '106': { id: '6a38daf9c8082fc3075823ee', uid: 'ip_192_168_41_106' },
  '13': { id: '6a38daf9c8082fc3075823f7', uid: 'ip_192_168_41_13' },
  '23': { id: '6a38daf9c8082fc3075823f8', uid: 'ip_192_168_41_23' },
  '24': { id: '6a38daf9c8082fc3075823f9', uid: 'ip_192_168_41_24' },
  '41': { id: '6a38daf9c8082fc3075823f4', uid: 'ip_192_168_41_41' },
};
const key = process.argv[2] || '13';
const t = TARGETS[key];
if (!t) throw new Error('unknown target');

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
const ctx = await browser.newContext();
await ctx.addCookies([{ name, value, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await ctx.newPage();
page.on('dialog', (d) => d.accept());
page.on('console', (m) => {
  const text = m.text();
  if (text.includes('[live-') || text.includes('error') || /hvc1|avc1|codec/i.test(text)) {
    console.log('C:', text.slice(0, 250));
  }
});
const url = `${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=1x1&fs=${t.id}`;
console.log('goto', url);
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
for (let i = 0; i < 40; i += 1) {
  const snap = await page.evaluate(({ uid, id }) => {
    const camsText = document.body.innerText.slice(0, 400);
    const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
    const cancelled = window.__nvrLiveMetrics?.getAll?.().cancelled ?? [];
    return {
      href: location.href,
      streams: document.querySelectorAll('video-stream').length,
      vids: document.querySelectorAll('video').length,
      complete: complete.map((s) => ({ uid: s.cameraUid, first: s.first_frame_ms, meta: s.metadata_ms })),
      cancelled: cancelled.slice(-3).map((s) => ({ uid: s.cameraUid, reason: s.cancelReason })),
      hit: complete.find((s) => s.cameraUid === uid || s.cameraId === id) || null,
      text: camsText,
    };
  }, t);
  console.log(`t=${i * 1}s streams=${snap.streams} vids=${snap.vids} complete=${snap.complete.length}`, JSON.stringify(snap.complete), snap.cancelled);
  if (snap.hit) {
    console.log('HIT', JSON.stringify(snap.hit, null, 2));
    break;
  }
  await page.waitForTimeout(1000);
}
await browser.close();
