import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], { cwd: ROOT, encoding: 'utf8' });
const [name, value] = py.stdout.trim().split('\n').pop().split('=');
const browser = await chromium.launch({ headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await browser.newContext();
await ctx.addCookies([{ name, value, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await ctx.newPage();
page.on('dialog', (d) => d.accept());
page.on('console', (m) => {
  const t = m.text();
  if (t.includes('[live-latency]') || t.includes('[live-media]') || t.includes('error')) console.log('C:', t.slice(0, 200));
});
await page.goto('http://127.0.0.1:8080/live?group=rml_6_corporate_office_2nd_floor&layout=5x5', { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(15000);
const snap = await page.evaluate(() => ({
  url: location.href,
  text: document.body.innerText.slice(0, 800),
  gridTotal: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-total'),
  mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
  metrics: typeof window.__nvrLiveMetrics,
  count: window.__nvrLiveMetrics?.summary?.().count,
  sample: window.__nvrLiveMetrics?.getAll?.().complete?.slice(0, 2),
  streams: document.querySelectorAll('video-stream').length,
}));
console.log(JSON.stringify(snap, null, 2));
await browser.close();
