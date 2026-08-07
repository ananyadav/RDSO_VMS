/**
 * Task 11 production smoke — deep-link Live View group + layout, capture WS/metrics.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const GROUP =
  process.env.LIVE_GROUP || 'rml_6_rashmi_6_paradigm_limited_precast_office';

function sessionCookie() {
  const py = spawnSync(path.join(ROOT, '.venv', 'Scripts', 'python.exe'), [path.join(ROOT, 'deploy', 'create-test-session.py')], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  if (py.status !== 0) throw new Error(py.stderr || py.stdout);
  const [name, value] = py.stdout.trim().split('\n').pop().split('=');
  return { name, value };
}

async function api(cookie, urlPath) {
  const res = await fetch(`${BASE}${urlPath}`, { headers: { Cookie: `${cookie.name}=${cookie.value}` } });
  const text = await res.text();
  let body = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* keep text */
  }
  return { status: res.status, body };
}

async function main() {
  const cookie = sessionCookie();
  const out = { base: BASE, at: new Date().toISOString(), tests: {} };

  out.tests.health = await api(cookie, '/api/health');
  out.tests.session = await api(cookie, '/api/auth/session');
  out.tests.liveConfig = await api(cookie, '/api/go2rtc/live-config');
  out.tests.go2rtcStatus = await api(cookie, '/api/go2rtc/status');
  // trim status
  if (out.tests.go2rtcStatus.body?.workers) {
    out.tests.go2rtcStatus.body = {
      running: out.tests.go2rtcStatus.body.running,
      streamCount: out.tests.go2rtcStatus.body.streamCount,
      cameraCount: out.tests.go2rtcStatus.body.cameraCount,
      workers: out.tests.go2rtcStatus.body.workers.map((w) => ({
        workerId: w.workerId,
        apiPort: w.apiPort,
        running: w.running,
        assignedCameraCount: w.assignedCameraCount,
        liveStreamCount: w.liveStreamCount,
      })),
    };
  }

  // One camera per worker via API
  const camsRes = await api(cookie, '/api/cameras');
  const cams = Array.isArray(camsRes.body) ? camsRes.body : [];
  const byWorker = {};
  for (const c of cams) {
    const wid = c.workerId ?? c.worker_id;
    if (!byWorker[wid] && c.online && c.is_active !== false) byWorker[wid] = c;
  }
  out.tests.samplePerWorker = Object.fromEntries(
    Object.entries(byWorker).map(([k, c]) => [
      k,
      { id: c.id, ip: c.ip_address, group: c.camera_group, uid: c.cameraUid || c.camera_uid },
    ]),
  );

  // Security HTTP
  out.tests.mediaLoggedOut = { status: (await fetch(`${BASE}/media/w1/api`)).status };
  out.tests.go2rtcWsLoggedOut = { status: (await fetch(`${BASE}/go2rtc/api/ws?src=x`)).status };
  out.tests.mediaAuthed = await api(cookie, '/media/w1/api');
  out.tests.mediaAuthed.statusOnly = out.tests.mediaAuthed.status;
  delete out.tests.mediaAuthed.body;
  out.tests.go2rtcWsAuthed = await api(cookie, '/go2rtc/api/ws?src=x');

  // Public worker ports
  out.tests.publicPorts = {};
  for (const p of [1984, 1985, 1986, 10000]) {
    try {
      const ctrl = AbortSignal.timeout(2500);
      const r = await fetch(`http://192.168.17.150:${p}/`, { signal: ctrl });
      out.tests.publicPorts[p] = r.status;
    } catch (e) {
      out.tests.publicPorts[p] = `unreachable:${e.cause?.code || e.name}`;
    }
  }

  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--autoplay-policy=no-user-gesture-required'],
  });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  await context.addCookies([
    {
      name: cookie.name,
      value: cookie.value,
      domain: '192.168.17.150',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
  ]);

  // --- Single camera / worker spot checks via temporary pages that load player ---
  // Use Live View with each sample's group first, then full 5x5.
  const wsAll = [];
  const mediaLogs = [];

  async function openLive(group, settleMs = 18000) {
    const page = await context.newPage();
    page.on('websocket', (ws) => wsAll.push(ws.url()));
    page.on('console', (msg) => {
      const t = msg.text();
      if (t.includes('[live-media]') || t.includes('[live-latency]')) mediaLogs.push(t.slice(0, 220));
    });
    const url = `${BASE}/live?group=${encodeURIComponent(group)}&layout=${encodeURIComponent('5x5')}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.waitForTimeout(settleMs);
    const snap = await page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      return {
        href: location.href,
        title: document.title,
        bodySnippet: document.body.innerText.slice(0, 500),
        cols: el?.getAttribute('data-live-grid-cols'),
        total: el?.getAttribute('data-live-grid-total'),
        mounted: el?.getAttribute('data-live-grid-mounted'),
        streamEligible: el?.getAttribute('data-live-grid-stream-eligible'),
        videoStreams: document.querySelectorAll('video-stream').length,
        videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
        metrics: window.__nvrLiveMetrics?.summary?.() ?? null,
        hasMetrics: typeof window.__nvrLiveMetrics,
      };
    });
    return { page, snap };
  }

  // Worker spot: open each sample group briefly
  out.tests.workerSpots = {};
  for (const [wid, sample] of Object.entries(out.tests.samplePerWorker)) {
    const before = wsAll.length;
    const { page, snap } = await openLive(sample.group, 14000);
    const ws = wsAll.slice(before).filter((u) => u.includes('/api/ws'));
    out.tests.workerSpots[wid] = {
      sample,
      snap: {
        mounted: snap.mounted,
        streamEligible: snap.streamEligible,
        videosPlaying: snap.videosPlaying,
        videoStreams: snap.videoStreams,
        metricsCount: snap.metrics?.count ?? null,
      },
      mediaWs: ws.filter((u) => u.includes(`/media/w${wid}/`)).slice(0, 3),
      anyMediaWs: ws.filter((u) => /\/media\/w\d+\//.test(u)).slice(0, 5),
      legacy: ws.filter((u) => u.includes('/go2rtc/api/ws')),
    };
    await page.close();
  }

  // Full 5x5 on group with many cameras
  const bigGroup =
    cams.filter((c) => c.camera_group === GROUP).length >= 25
      ? GROUP
      : Object.values(out.tests.samplePerWorker)[0]?.group || GROUP;

  const { page, snap } = await openLive(bigGroup, 5000);
  await page.evaluate(() => window.__nvrLiveMetrics?.clear?.());
  await page.waitForTimeout(20000);
  const settle = await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    return {
      href: location.href,
      bodySnippet: document.body.innerText.slice(0, 400),
      cols: el?.getAttribute('data-live-grid-cols'),
      total: el?.getAttribute('data-live-grid-total'),
      mounted: el?.getAttribute('data-live-grid-mounted'),
      streamEligible: el?.getAttribute('data-live-grid-stream-eligible'),
      videoStreams: document.querySelectorAll('video-stream').length,
      videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      metrics: window.__nvrLiveMetrics?.summary?.() ?? null,
    };
  });
  out.tests.grid5x5 = { group: bigGroup, settle };

  // Scroll away / back
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = Math.min(el.scrollHeight, 2500);
  });
  await page.waitForTimeout(4000);
  const scrolled = await page.evaluate(() => ({
    mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
    streamEligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
    videoStreams: document.querySelectorAll('video-stream').length,
    cancelled: window.__nvrLiveMetrics?.summary?.()?.cancelledCount ?? null,
  }));
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 0;
  });
  await page.waitForTimeout(8000);
  const back = await page.evaluate(() => ({
    mounted: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-mounted'),
    streamEligible: document.querySelector('[data-live-grid-cols]')?.getAttribute('data-live-grid-stream-eligible'),
    videoStreams: document.querySelectorAll('video-stream').length,
    videosPlaying: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
    metrics: window.__nvrLiveMetrics?.summary?.() ?? null,
  }));
  out.tests.scroll = { scrolled, back };

  // Fullscreen
  const tile = page.locator('[data-live-grid-cols] .group').first();
  if (await tile.count()) {
    await tile.dblclick().catch(() => {});
    await page.waitForTimeout(2500);
    out.tests.fullscreen = await page.evaluate(() => ({
      streams: document.querySelectorAll('video-stream').length,
      playing: [...document.querySelectorAll('video')].filter((v) => v.videoWidth > 0).length,
      hasEscHint: /ESC/i.test(document.body.innerText),
    }));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(2000);
    out.tests.fullscreenReturn = {
      mounted: await page.locator('[data-live-grid-cols]').getAttribute('data-live-grid-mounted'),
    };
  }

  // PTZ presence
  out.tests.ptz = {
    buttonCount: await page.locator('button', { hasText: /PTZ|Zoom|Iris/i }).count(),
  };

  const mediaWs = wsAll.filter((u) => /\/media\/w\d+\/api\/ws/.test(u));
  const legacy = wsAll.filter((u) => u.includes('/go2rtc/api/ws'));
  out.tests.websockets = {
    mediaCount: mediaWs.length,
    legacyCount: legacy.length,
    workersHit: [...new Set(mediaWs.map((u) => (u.match(/\/media\/(w\d+)\//) || [])[1]))],
    sample: mediaWs.slice(0, 10),
    directOnly: mediaWs.length > 0 && legacy.length === 0,
  };
  out.tests.mediaLogs = mediaLogs.slice(0, 15);

  fs.writeFileSync(path.join(ROOT, 'deploy', 'task11-prod-smoke.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
