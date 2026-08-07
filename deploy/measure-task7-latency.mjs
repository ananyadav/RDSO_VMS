/**
 * Task 7 — per-camera cold starts via fullscreen (eager) on Nginx :8080.
 * Uses Edge for HEVC decode support.
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
  { ip: '192.168.41.13', id: '6a38daf9c8082fc3075823f7', uid: 'ip_192_168_41_13' },
  { ip: '192.168.41.23', id: '6a38daf9c8082fc3075823f8', uid: 'ip_192_168_41_23' },
  { ip: '192.168.41.24', id: '6a38daf9c8082fc3075823f9', uid: 'ip_192_168_41_24' },
  { ip: '192.168.41.41', id: '6a38daf9c8082fc3075823f4', uid: 'ip_192_168_41_41' },
];
const RUNS = 3;

function sessionCookie() {
  const py = spawnSync(
    path.join(ROOT, '.venv', 'Scripts', 'python.exe'),
    [path.join(ROOT, 'deploy', 'create-test-session.py')],
    { cwd: ROOT, encoding: 'utf8' },
  );
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function waitSample(page, uid, id, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const hit = await page.evaluate(
      ({ uid, id }) => {
        const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
        return complete.find((s) => s.cameraUid === uid || s.cameraId === id) || null;
      },
      { uid, id },
    );
    if (hit) return hit;
    await page.waitForTimeout(500);
  }
  return null;
}

async function main() {
  const label = process.argv[2] || 'run';
  // Ensure online flags again
  spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'backend', 'scripts', 'task7_client_ok.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });

  const cookie = sessionCookie();
  const browser = await chromium.launch({
    channel: 'msedge',
    headless: false,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext();
  await context.addCookies([
    { name: cookie.name, value: cookie.value, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax' },
  ]);
  const page = await context.newPage();
  page.on('dialog', (d) => d.accept());

  const results = [];
  for (const t of TARGETS) {
    const camRuns = [];
    for (let i = 0; i < RUNS; i += 1) {
      await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
      const url = `${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=1x1&fs=${t.id}`;
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
      await page.waitForTimeout(2000);
      await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
      // reload fullscreen path once metrics API exists
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
      const sample = await waitSample(page, t.uid, t.id, 30000);
      camRuns.push({
        run: i + 1,
        metadata_ms: sample?.metadata_ms ?? null,
        first_frame_ms: sample?.first_frame_ms ?? null,
        total_visible_ms: sample?.total_visible_ms ?? null,
        queue_wait_ms: sample?.queue_wait_ms ?? null,
        workerId: sample?.workerId ?? null,
      });
      console.error(`${label} ${t.ip} run ${i + 1}: first=${sample?.first_frame_ms} meta=${sample?.metadata_ms}`);
      // close fullscreen / leave page between cold starts
      await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=1x1`, {
        waitUntil: 'domcontentloaded',
      });
      await page.waitForTimeout(1500);
    }
    results.push({ ...t, runs: camRuns });
  }

  const out = { label, results };
  const outPath = path.join(ROOT, 'deploy', `task7-latency-${label}.json`);
  fs.writeFileSync(outPath, JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
