/**
 * Task 10: 5x5 cold-wall latency for canary (~1s GOP) vs non-canary (longer GOP).
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = 'http://127.0.0.1:8080';
const ART = path.join(ROOT, 'deploy', 'task10');

const selection = JSON.parse(fs.readFileSync(path.join(ART, 'canary-selection.json'), 'utf8'));
const apply = JSON.parse(fs.readFileSync(path.join(ART, 'canary-apply-results.json'), 'utf8'));
const okIps = new Set(apply.filter((r) => r.ok).map((r) => r.ip));
const canary = selection.cameras.filter((c) => okIps.has(c.ip));
const CANARY_UIDS = new Set(canary.map((c) => c.cameraUid));

// Prefer densest canary groups for 5x5 walls
const byGroup = {};
for (const c of canary) {
  byGroup[c.camera_group] = byGroup[c.camera_group] || [];
  byGroup[c.camera_group].push(c);
}
const GROUPS = Object.entries(byGroup)
  .sort((a, b) => b[1].length - a[1].length)
  .slice(0, 2)
  .map(([g]) => g);

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
  return s[Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1)];
}

function summarize(samples) {
  const keys = ['queue_wait_ms', 'player_start_ms', 'metadata_ms', 'first_frame_ms', 'total_visible_ms'];
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

async function markOnline() {
  const ips = canary.map((c) => c.ip);
  const script = `
import asyncio, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(r${JSON.stringify(ROOT)})
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from app.core.database import camera_collection
IPS = ${JSON.stringify(ips)}
async def main():
    now = datetime.now(timezone.utc).isoformat()
    for ip in IPS:
        await camera_collection.update_one(
            {'ip_address': ip},
            {'$set': {
                'stream_health_ok': True,
                'stream_health_alarm': False,
                'stream_health_strikes': 0,
                'stream_health_category': 'online',
                'stream_health_message': '',
                'stream_health_checked_at': now,
            }},
        )
    print('marked', len(IPS))
asyncio.run(main())
`;
  const tmp = path.join(ART, '_mark_online.py');
  fs.writeFileSync(tmp, script);
  spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [tmp], { cwd: ROOT, encoding: 'utf8' });
}

async function collectWall(page, group, timeoutMs = 55000) {
  await page.goto(`${BASE}/live?group=${encodeURIComponent(group)}&layout=5x5`, {
    waitUntil: 'domcontentloaded',
    timeout: 120000,
  });
  const start = Date.now();
  let last = [];
  while (Date.now() - start < timeoutMs) {
    last = await page.evaluate(() => {
      const complete = window.__nvrLiveMetrics?.getAll?.().complete ?? [];
      return complete
        .filter((s) => s.profile === 'sub')
        .map((s) => ({
          cameraUid: s.cameraUid,
          cameraId: s.cameraId,
          workerId: s.workerId,
          queue_wait_ms: s.queue_wait_ms,
          player_start_ms: s.player_start_ms,
          metadata_ms: s.metadata_ms,
          first_frame_ms: s.first_frame_ms,
          total_visible_ms: s.total_visible_ms,
        }));
    });
    // unique cameras
    const u = new Set(last.map((s) => s.cameraUid));
    if (u.size >= 8) break;
    await page.waitForTimeout(500);
  }
  // first sample per uid
  const by = new Map();
  for (const s of last) {
    if (!by.has(s.cameraUid)) by.set(s.cameraUid, s);
  }
  return [...by.values()];
}

async function main() {
  markOnline();
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
  const consoleErrors = [];
  page.on('console', (msg) => {
    const t = msg.text();
    if (t.includes('[live-media]') && t.includes('mode=direct')) mediaLogs.push(t);
    if (msg.type() === 'error') consoleErrors.push(t.slice(0, 250));
  });

  const coldCanary = [];
  const coldOther = [];

  console.error('groups', GROUPS);
  for (let run = 1; run <= 3; run += 1) {
    for (const group of GROUPS) {
      await page.evaluate(() => window.__nvrLiveMetrics?.clear?.()).catch(() => {});
      const samples = await collectWall(page, group);
      for (const s of samples) {
        const row = { run, group, ...s, canary: CANARY_UIDS.has(s.cameraUid) };
        if (row.canary) coldCanary.push(row);
        else coldOther.push(row);
      }
      const cN = samples.filter((s) => CANARY_UIDS.has(s.cameraUid)).length;
      const oN = samples.length - cN;
      console.error(`run ${run} group=${group} samples=${samples.length} canary=${cN} other=${oN}`);
      await page.goto('about:blank');
      await page.waitForTimeout(1000);
    }
  }

  const slowest = [...coldCanary]
    .filter((s) => typeof s.first_frame_ms === 'number')
    .sort((a, b) => b.first_frame_ms - a.first_frame_ms)
    .slice(0, 8);

  const payload = {
    groups: GROUPS,
    canaryUids: [...CANARY_UIDS],
    coldCanary,
    coldOther,
    canarySummary: summarize(coldCanary),
    otherSummary: summarize(coldOther),
    slowestCanary: slowest,
    directMediaLogs: mediaLogs.length,
    consoleErrors: consoleErrors.slice(0, 20),
  };
  fs.writeFileSync(path.join(ART, 'canary-live-latency.json'), JSON.stringify(payload, null, 2));
  console.log(JSON.stringify({
    groups: GROUPS,
    canary: payload.canarySummary,
    other: payload.otherSummary,
    slowest: slowest.map((s) => ({ uid: s.cameraUid, first: s.first_frame_ms, meta: s.metadata_ms, group: s.group })),
    direct: mediaLogs.length,
    errors: consoleErrors.length,
  }, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
