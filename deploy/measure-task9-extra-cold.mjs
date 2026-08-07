import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
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
const UID_TO_IP = {
  ip_192_168_41_106: '192.168.41.106',
  ip_192_168_41_13: '192.168.41.13',
  ip_192_168_41_23: '192.168.41.23',
  ip_192_168_41_24: '192.168.41.24',
  ip_192_168_41_41: '192.168.41.41',
};

function pct(vals, p) {
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1)];
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

const label = process.argv[2] || 'h265';
const file = path.join(ROOT, 'deploy', `task9-latency-${label}.json`);
const j = JSON.parse(fs.readFileSync(file, 'utf8'));

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
await page.goto(`${BASE}/live?group=${encodeURIComponent(GROUP)}&layout=5x5`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
let last = [];
for (let i = 0; i < 100; i += 1) {
  last = await page.evaluate((uids) => {
    const c = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
    return c.filter((s) => uids.includes(s.cameraUid) && s.profile === 'sub');
  }, TARGET_UIDS);
  if (new Set(last.map((s) => s.cameraUid)).size >= 5) break;
  await page.waitForTimeout(500);
}
const by = new Map();
for (const s of last) if (!by.has(s.cameraUid)) by.set(s.cameraUid, s);
const run = Math.max(0, ...j.cold.map((s) => s.run || 0)) + 1;
for (const s of by.values()) {
  const row = {
    phase: 'cold',
    run,
    ip: UID_TO_IP[s.cameraUid],
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
  };
  j.cold.push(row);
  console.error(`EXTRA ${row.ip} first=${row.first_frame_ms} meta=${row.metadata_ms}`);
}
j.coldSummary = summarize(j.cold);
fs.writeFileSync(file, JSON.stringify(j, null, 2));
console.log(JSON.stringify({ coldN: j.cold.length, first: j.coldSummary.first_frame_ms, meta: j.coldSummary.metadata_ms }, null, 2));
await browser.close();
