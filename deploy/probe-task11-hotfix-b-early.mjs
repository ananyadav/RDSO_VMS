/** Early settle probe — detect idle-black tiles before full connect. */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const GROUP = 'rml_6_rashmi_6_paradigm_limited_precast_office';
const IPS = ['192.168.11.27', '192.168.11.30', '192.168.11.31', '192.168.11.40'];

const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [
  path.join(ROOT, 'deploy', 'create-test-session.py'),
], { cwd: ROOT, encoding: 'utf8' });
const [name, value] = py.stdout.trim().split('\n').pop().split('=');

const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addCookies([{ name, value, domain: '192.168.17.150', path: '/', httpOnly: true, sameSite: 'Lax' }]);
const page = await ctx.newPage();
await page.goto(`http://192.168.17.150/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, { waitUntil: 'domcontentloaded', timeout: 120000 });

for (const ms of [2000, 5000, 8000, 12000]) {
  await page.waitForTimeout(ms === 2000 ? 2000 : ms - (ms === 5000 ? 2000 : ms === 8000 ? 5000 : 8000));
  const snap = await page.evaluate((targets) => {
    const read = (ip) => {
      const h3 = [...document.querySelectorAll('.group h3')].find((el) => el.textContent?.includes(ip));
      const card = h3?.closest('.group');
      const video = card?.querySelector('video');
      const overlay = card?.querySelector('.animate-pulse');
      return {
        ip,
        found: Boolean(h3),
        vw: video?.videoWidth ?? 0,
        mode: card?.querySelector('video-stream .mode')?.textContent?.trim() ?? null,
        overlay: Boolean(overlay),
      };
    };
    return targets.map(read);
  }, IPS);
  console.log(`@ ${ms}ms`, JSON.stringify(snap));
}
await browser.close();
