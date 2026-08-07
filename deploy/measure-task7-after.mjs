/**
 * Quick Task 7 after-latency for one camera via Edge fullscreen.
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import fs from 'node:fs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://127.0.0.1:8080';
const GROUP = 'rml_6_corporate_office_2nd_floor';
const TARGETS = [
  { ip: '192.168.41.106', id: '6a38daf9c8082fc3075823ee', uid: 'ip_192_168_41_106' },
  { ip: '192.168.41.24', id: '6a38daf9c8082fc3075823f9', uid: 'ip_192_168_41_24' },
  { ip: '192.168.41.41', id: '6a38daf9c8082fc3075823f4', uid: 'ip_192_168_41_41' },
];

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function main() {
  spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'backend', 'scripts', 'task7_client_ok.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  const cookie = sessionCookie();
  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext();
  await context.addCookies([
    { name: cookie.name, value: cookie.value, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
  const page = await context.newPage();
  page.on('dialog', (d) => d.accept());

  const out = [];
  for (const t of TARGETS) {
    const runs = [];
    for (let i = 0; i < 3; i += 1) {
      await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
      const url = `${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=1x1&fs=${t.id}`;
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
      let sample = null;
      for (let w = 0; w < 40; w += 1) {
        sample = await page.evaluate(
          ({ uid, id }) => {
            const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
            return complete.find((s) => s.cameraUid === uid || s.cameraId === id) || null;
          },
          { uid: t.uid, id: t.id },
        );
        if (sample) break;
        await page.waitForTimeout(500);
      }
      runs.push({
        run: i + 1,
        metadata_ms: sample?.metadata_ms ?? null,
        first_frame_ms: sample?.first_frame_ms ?? null,
        total_visible_ms: sample?.total_visible_ms ?? null,
        queue_wait_ms: sample?.queue_wait_ms ?? null,
      });
      console.error(`after ${t.ip} run ${i + 1}: first=${sample?.first_frame_ms} meta=${sample?.metadata_ms}`);
      await page.goto('about:blank');
      await page.waitForTimeout(1000);
    }
    out.push({ ...t, runs });
  }
  const payload = { label: 'after', out };
  fs.writeFileSync(path.join(ROOT, 'deploy', 'task7-latency-after.json'), JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
