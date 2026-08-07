/**
 * Task 9: cold + warm Live View latency for the 5 test cameras (substream 102).
 * Uses group grid (not fullscreen) so profile stays on sub — fullscreen defaults to main.
 * Usage: node deploy/measure-task9-latency.mjs <label>
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://127.0.0.1:8080';
const GROUP = 'rml_6_corporate_office_2nd_floor';
const LABEL = process.argv[2] || 'run';
const TARGET_UIDS = [
  'ip_192_168_41_106',
  'ip_192_168_41_13',
  'ip_192_168_41_23',
  'ip_192_168_41_24',
  'ip_192_168_41_41',
];
const UID_TO_IP = {
  ip_192_168_41_106: '192.168.41.106',
  ip_192_168_41_13: '192.168.41.13',
  ip_192_168_41_23: '192.168.41.23',
  ip_192_168_41_24: '192.168.41.24',
  ip_192_168_41_41: '192.168.41.41',
};

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

function pct(vals, p) {
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  const idx = Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1);
  return s[Math.max(0, idx)];
}

function summarize(samples) {
  const keys = [
    'queue_wait_ms',
    'player_start_ms',
    'transport_ms',
    'metadata_ms',
    'playing_ms',
    'first_frame_ms',
    'total_visible_ms',
  ];
  const out = {};
  for (const k of keys) {
    const vals = samples.map((s) => s[k]).filter((v) => typeof v === 'number');
    out[k] = {
      n: vals.length,
      p50: pct(vals, 50),
      p95: pct(vals, 95),
      max: vals.length ? Math.max(...vals) : null,
      all: [...vals].sort((a, b) => a - b),
    };
  }
  return out;
}

async function openGrid(page) {
  const url = `${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`;
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
}

async function collectTargets(page, timeoutMs = 45000) {
  const start = Date.now();
  let last = [];
  while (Date.now() - start < timeoutMs) {
    last = await page.evaluate((uids) => {
      const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
      return complete
        .filter((s) => uids.includes(s.cameraUid) && s.profile === 'sub')
        .map((s) => ({
          cameraUid: s.cameraUid,
          workerId: s.workerId,
          profile: s.profile,
          queue_wait_ms: s.queue_wait_ms,
          player_start_ms: s.player_start_ms,
          transport_ms: s.transport_ms,
          metadata_ms: s.metadata_ms,
          playing_ms: s.playing_ms,
          first_frame_ms: s.first_frame_ms,
          total_visible_ms: s.total_visible_ms,
        }));
    }, TARGET_UIDS);
    // Prefer unique cameras; if we have 5, done.
    const seen = new Set(last.map((s) => s.cameraUid));
    if (seen.size >= 5) return last;
    await page.waitForTimeout(500);
  }
  return last;
}

async function main() {
  // Ensure cameras marked healthy before measuring.
  spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'backend', 'scripts', 'task7_mark_online.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
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

  const mediaLogs = [];
  const codecHints = [];
  const consoleErrors = [];
  page.on('console', (msg) => {
    const text = msg.text();
    if (text.includes('[live-media]') && text.includes('mode=direct')) mediaLogs.push(text);
    const m = text.match(/codecs="([^"]+)"/i);
    if (m) codecHints.push(m[1]);
    if (/stream\.onmessge.*mse/i.test(text)) codecHints.push(text.slice(0, 200));
    if (msg.type() === 'error') consoleErrors.push(text.slice(0, 300));
  });

  const cold = [];
  const warm = [];

  // 3 cold page loads — each yields up to 5 target samples
  for (let run = 1; run <= 3; run += 1) {
    await page.evaluate(() => window.__nvrLiveMetrics?.clear?.()).catch(() => {});
    await openGrid(page);
    const batch = await collectTargets(page, 50000);
    // Keep first completion per uid this run
    const byUid = new Map();
    for (const s of batch) {
      if (!byUid.has(s.cameraUid)) byUid.set(s.cameraUid, s);
    }
    for (const s of byUid.values()) {
      const row = { phase: 'cold', run, ip: UID_TO_IP[s.cameraUid], ...s };
      cold.push(row);
      console.error(
        `COLD ${row.ip} #${run}: first=${row.first_frame_ms} meta=${row.metadata_ms} player=${row.player_start_ms} queue=${row.queue_wait_ms}`,
      );
    }
    console.error(`cold run ${run}: ${byUid.size}/5 targets`);
    await page.goto('about:blank');
    await page.waitForTimeout(1500);
  }

  // Warm reopen: leave grid up, clear metrics, reload once
  await openGrid(page);
  await collectTargets(page, 40000);
  await page.waitForTimeout(3000);
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.()).catch(() => {});
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 120000 });
  const warmBatch = await collectTargets(page, 40000);
  const warmByUid = new Map();
  for (const s of warmBatch) {
    if (!warmByUid.has(s.cameraUid)) warmByUid.set(s.cameraUid, s);
  }
  for (const s of warmByUid.values()) {
    const row = { phase: 'warm', ip: UID_TO_IP[s.cameraUid], ...s };
    warm.push(row);
    console.error(`WARM ${row.ip}: first=${row.first_frame_ms} meta=${row.metadata_ms}`);
  }

  // Codec string from console + page inspect
  let browserCodec = {
    hints: [...new Set(codecHints)].slice(0, 20),
    mseFromPage: null,
  };
  browserCodec.mseFromPage = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll('video-stream')) {
      try {
        out.push({
          src: String(el.src || '').slice(0, 100),
          mseCodec: el.mse?.codec || null,
        });
      } catch {
        /* ignore */
      }
    }
    return out;
  });

  const payload = {
    label: LABEL,
    cold,
    warm,
    coldSummary: summarize(cold),
    warmSummary: summarize(warm),
    directMediaLogs: mediaLogs.length,
    browserCodec,
    consoleErrors: consoleErrors.slice(0, 40),
  };
  const outPath = path.join(ROOT, 'deploy', `task9-latency-${LABEL}.json`);
  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
