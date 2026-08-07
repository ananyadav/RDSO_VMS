/**
 * Task 8: measure T2→T3 (player_start_ms) for Task 6/7 test cameras.
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://127.0.0.1:8080';
const GROUP = 'rml_6_corporate_office_2nd_floor';
const TARGET_UIDS = [
  'ip_192_168_41_106',
  'ip_192_168_41_13',
  'ip_192_168_41_23',
  'ip_192_168_41_24',
  'ip_192_168_41_41',
];

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

function summarize(samples) {
  const keys = ['queue_wait_ms', 'player_start_ms', 'metadata_ms', 'first_frame_ms', 'total_visible_ms'];
  const out = {};
  for (const k of keys) {
    const vals = samples.map((s) => s[k]).filter((v) => typeof v === 'number').sort((a, b) => a - b);
    if (!vals.length) {
      out[k] = { n: 0, min: null, p50: null, max: null, all: [] };
      continue;
    }
    out[k] = {
      n: vals.length,
      min: vals[0],
      p50: vals[Math.floor(vals.length / 2)],
      max: vals[vals.length - 1],
      all: vals,
    };
  }
  return out;
}

async function main() {
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

  const mediaLogs = [];
  const ensureLogs = [];
  page.on('console', (msg) => {
    const text = msg.text();
    if (text.includes('[live-media]') && text.includes('mode=direct')) mediaLogs.push(text);
    if (text.includes('post-slot player-ensure')) ensureLogs.push(text);
  });

  const collected = [];

  for (let pass = 0; pass < 2; pass += 1) {
    await page.evaluate(() => window.__nvrLiveMetrics?.clear?.()).catch(() => {});
    const url = `${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=2x2`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });

    // Wait until we have metrics for as many target cameras as appear.
    for (let w = 0; w < 60; w += 1) {
      const batch = await page.evaluate((uids) => {
        const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
        return complete.filter((s) => uids.includes(s.cameraUid));
      }, TARGET_UIDS);
      if (batch.length >= 4) break;
      await page.waitForTimeout(500);
    }

    const batch = await page.evaluate((uids) => {
      const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
      return complete
        .filter((s) => uids.includes(s.cameraUid))
        .map((s) => ({
          cameraUid: s.cameraUid,
          queue_wait_ms: s.queue_wait_ms,
          player_start_ms: s.player_start_ms,
          metadata_ms: s.metadata_ms,
          first_frame_ms: s.first_frame_ms,
          total_visible_ms: s.total_visible_ms,
        }));
    }, TARGET_UIDS);

    for (const s of batch) collected.push({ pass: pass + 1, ...s });
    console.error(`pass ${pass + 1}: got ${batch.length} target samples`);
    for (const s of batch) {
      console.error(
        `  ${s.cameraUid} queue=${s.queue_wait_ms} player=${s.player_start_ms} meta=${s.metadata_ms} first=${s.first_frame_ms} total=${s.total_visible_ms}`,
      );
    }

    await page.goto('about:blank');
    await page.waitForTimeout(1500);
  }

  const payload = {
    label: 'task8-after',
    samples: collected,
    summary: summarize(collected),
    directMediaLogs: mediaLogs.length,
    ensureAfterSlotLogs: ensureLogs.slice(0, 10),
  };
  console.log(JSON.stringify(payload, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
