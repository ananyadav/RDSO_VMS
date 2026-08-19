/**
 * Task 12B — production Live View soak (measurement only; no VMS code changes).
 * ~30 minutes: walls / scroll / view-change / settled.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const BASE = process.env.PROD_BASE || 'http://192.168.17.150';
const OUT = path.join(ROOT, 'deploy', 'task12b-soak.json');

const GROUPS = {
  w1: 'rml_6_isp_labour_housing_2',
  w2: 'rml_6_isp_seamless',
  w3: 'rml_3_kipl_kipl_all',
  mixed: 'rml_6_rashmi_6_paradigm_limited_biomass',
};

const WALL_MS = Number(process.env.SOAK_WALL_MS || 5 * 60 * 1000);
const SCROLL_MS = Number(process.env.SOAK_SCROLL_MS || 10 * 60 * 1000);
const SWITCH_MS = Number(process.env.SOAK_SWITCH_MS || 10 * 60 * 1000);
const SETTLE_MS = Number(process.env.SOAK_SETTLE_MS || 5 * 60 * 1000);

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

async function api(cookie, urlPath, opts = {}) {
  const t0 = Date.now();
  const res = await fetch(`${BASE}${urlPath}`, {
    ...opts,
    headers: {
      Cookie: `${cookie.name}=${cookie.value}`,
      ...(opts.headers || {}),
    },
  });
  const text = await res.text();
  let body = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* keep */
  }
  return { status: res.status, ms: Date.now() - t0, body };
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function writeOut(data) {
  fs.writeFileSync(OUT, JSON.stringify(data, null, 2));
}

function nowIso() {
  return new Date().toISOString();
}

function wsSrc(url) {
  try {
    return new URL(url).searchParams.get('src');
  } catch {
    return url;
  }
}

async function main() {
  const started = Date.now();
  const cookie = sessionCookie();
  const report = {
    startedAt: nowIso(),
    base: BASE,
    phases: {},
    samples: [],
    security: {},
    apiHealth: [],
    workersBefore: null,
    workersAfter: null,
    ws: { opened: 0, closed: 0, legacy: 0, maxConcurrent: 0, duplicateOpenEvents: 0 },
    black: { permanent: [], recovered: 0, connectingOver10s: [] },
    camerasTraversed: new Set(),
    notes: [],
  };

  const log = (msg) => {
    console.log(`[${new Date().toISOString()}] ${msg}`);
  };

  // --- Security + pre health ---
  try {
    const ctrl = AbortSignal.timeout(3000);
    const r = await fetch(`${BASE.replace(/:\\d+$/, '')}:10000/api/health`.replace(
      'http://192.168.17.150:10000',
      'http://192.168.17.150:10000',
    ), { signal: ctrl });
    report.security.external10000 = { reachable: true, status: r.status };
  } catch (e) {
    report.security.external10000 = {
      reachable: false,
      error: e.cause?.code || e.name,
    };
  }
  report.security.mediaLogout = { status: (await fetch(`${BASE}/media/w1/api`)).status };
  report.security.go2rtcWsLogout = { status: (await fetch(`${BASE}/go2rtc/api/ws?src=x`)).status };
  report.security.go2rtcWsAuthed = await api(cookie, '/go2rtc/api/ws?src=x');
  report.security.badStream = await api(cookie, '/api/go2rtc/media-auth?src=nope');
  report.security.nginxHealth = await api(cookie, '/api/health');
  const st = await api(cookie, '/api/go2rtc/status');
  report.workersBefore = (st.body?.workers || []).map((w) => ({
    workerId: w.workerId,
    running: w.running,
    apiPort: w.apiPort,
    assigned: w.assignedCameraCount,
    streams: w.liveStreamCount,
  }));
  log(`workers before: ${JSON.stringify(report.workersBefore)}`);

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
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  page.on('dialog', (d) => d.accept());

  const openBySrc = new Map();
  const wsEvents = [];
  page.on('websocket', (ws) => {
    const url = ws.url();
    const src = wsSrc(url);
    const rec = { url, src, t: Date.now(), open: true };
    wsEvents.push(rec);
    report.ws.opened += 1;
    if (url.includes('/go2rtc/api/ws')) report.ws.legacy += 1;
    const n = (openBySrc.get(src) || 0) + 1;
    openBySrc.set(src, n);
    if (n > 1 && url.includes('/media/w')) report.ws.duplicateOpenEvents += 1;
    const concurrent = [...openBySrc.values()].reduce((a, b) => a + b, 0);
    if (concurrent > report.ws.maxConcurrent) report.ws.maxConcurrent = concurrent;
    ws.on('close', () => {
      rec.open = false;
      rec.closedAt = Date.now();
      report.ws.closed += 1;
      const left = (openBySrc.get(src) || 1) - 1;
      if (left <= 0) openBySrc.delete(src);
      else openBySrc.set(src, left);
    });
  });

  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable').catch(() => {});

  async function memSample(label) {
    let heap = null;
    try {
      heap = await page.evaluate(() => {
        const m = performance.memory;
        if (!m) return null;
        return {
          usedMB: Math.round(m.usedJSHeapSize / 1048576),
          totalMB: Math.round(m.totalJSHeapSize / 1048576),
        };
      });
    } catch {
      heap = null;
    }
    let metrics = null;
    try {
      const m = await cdp.send('Performance.getMetrics');
      const map = Object.fromEntries((m.metrics || []).map((x) => [x.name, x.value]));
      metrics = {
        jsHeapUsedMB: map.JSHeapUsedSize ? Math.round(map.JSHeapUsedSize / 1048576) : null,
        jsHeapTotalMB: map.JSHeapTotalSize ? Math.round(map.JSHeapTotalSize / 1048576) : null,
        nodes: map.Nodes ?? null,
        jsEventListeners: map.JSEventListeners ?? null,
      };
    } catch {
      metrics = null;
    }
    const sample = { t: nowIso(), elapsedSec: Math.round((Date.now() - started) / 1000), label, heap, metrics };
    report.samples.push(sample);
    log(`mem ${label} heap=${JSON.stringify(heap)} cdp=${JSON.stringify(metrics)}`);
    return sample;
  }

  async function healthTick(label) {
    const h = await api(cookie, '/api/health');
    const s = await api(cookie, '/api/go2rtc/status');
    report.apiHealth.push({
      t: nowIso(),
      label,
      healthMs: h.ms,
      health: h.body,
      workers: (s.body?.workers || []).map((w) => ({
        workerId: w.workerId,
        running: w.running,
        streams: w.liveStreamCount,
      })),
    });
    return h;
  }

  async function gridSnap() {
    return page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      const cards = [...document.querySelectorAll('.group')];
      const tiles = cards.map((card) => {
        const ip = card.querySelector('h3')?.textContent?.trim() || '';
        const online = /Online/i.test(card.querySelector('span')?.textContent || '');
        const video = card.querySelector('video');
        const overlay = card.querySelector('.animate-pulse');
        const vs = card.querySelector('video-stream');
        return {
          ip,
          online,
          videoWidth: video?.videoWidth ?? 0,
          paused: video?.paused ?? null,
          overlay: Boolean(overlay),
          overlayText: overlay?.textContent?.trim()?.slice(0, 40) || null,
          hasVs: Boolean(vs),
          mode: vs?.querySelector?.('.mode')?.textContent?.trim() || null,
        };
      });
      const playing = tiles.filter((t) => t.videoWidth > 0).length;
      const connecting = tiles.filter((t) => t.overlay).length;
      const onlineNoVideo = tiles.filter((t) => t.online && t.videoWidth === 0);
      return {
        href: location.href,
        cols: el?.getAttribute('data-live-grid-cols'),
        total: Number(el?.getAttribute('data-live-grid-total') || 0),
        mounted: Number(el?.getAttribute('data-live-grid-mounted') || 0),
        eligible: Number(el?.getAttribute('data-live-grid-stream-eligible') || 0),
        cardCount: cards.length,
        playing,
        connecting,
        onlineNoVideo: onlineNoVideo.map((t) => t.ip),
        tiles: tiles.slice(0, 40),
      };
    });
  }

  async function openLive(group, waitMs = 18000) {
    const url = `${BASE}/live?group=${encodeURIComponent(group)}&layout=${encodeURIComponent('5x5')}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await sleep(waitMs);
    const snap = await gridSnap();
    for (const t of snap.tiles) {
      if (t.ip) report.camerasTraversed.add(t.ip);
    }
    return snap;
  }

  async function collectVisibleNames() {
    const names = await page.evaluate(() =>
      [...document.querySelectorAll('.group h3')].map((el) => el.textContent?.trim()).filter(Boolean),
    );
    for (const n of names) report.camerasTraversed.add(n);
    return names;
  }

  await memSample('start');
  await healthTick('start');

  // ===== Phase 1: walls covering w1/w2/w3 =====
  const wallDeadline = Date.now() + WALL_MS;
  const walls = {};
  for (const [key, group] of Object.entries(GROUPS)) {
    log(`wall ${key} ${group}`);
    const snap = await openLive(group, 16000);
    const after10 = await (async () => {
      await sleep(4000);
      return gridSnap();
    })();
    const stillBlack = after10.onlineNoVideo.filter((ip) => !after10.tiles.find((t) => t.ip === ip && t.overlay));
    walls[key] = {
      group,
      settle: snap,
      after20s: after10,
      permanentBlackCandidates: stillBlack,
    };
    if (stillBlack.length) {
      report.black.permanent.push({ phase: `wall-${key}`, ips: stillBlack });
    }
    await healthTick(`wall-${key}`);
    await memSample(`wall-${key}`);
    if (Date.now() > wallDeadline) break;
  }
  while (Date.now() < wallDeadline) {
    await sleep(Math.min(15000, wallDeadline - Date.now()));
    await healthTick('wall-hold');
    await memSample('wall-hold');
  }
  report.phases.walls = walls;

  // Worker media paths from WS so far
  const workersHit = [...new Set(wsEvents.filter((e) => /\/media\/w\d+\//.test(e.url)).map((e) => (e.url.match(/\/media\/(w\d+)\//) || [])[1]))];
  report.phases.walls.workersHit = workersHit;

  // ===== Phase 2: rapid scroll =====
  log('scroll stress start');
  await openLive(GROUPS.w1, 12000);
  const scrollStarted = Date.now();
  let scrollCycles = 0;
  let positions = 0;
  while (Date.now() - scrollStarted < SCROLL_MS) {
    await page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      if (!el) return;
      const max = Math.max(0, el.scrollHeight - el.clientHeight);
      const step = Math.max(180, Math.floor(el.clientHeight * 0.85));
      el.scrollTop = Math.min(max, el.scrollTop + step);
      if (el.scrollTop >= max - 4) el.scrollTop = 0;
    });
    positions += 1;
    await collectVisibleNames();
    scrollCycles += 1;
    await sleep(450);
    if (scrollCycles % 20 === 0) {
      const snap = await gridSnap();
      const concurrent = [...openBySrc.values()].reduce((a, b) => a + b, 0);
      log(
        `scroll cycle=${scrollCycles} mounted=${snap.mounted} eligible=${snap.eligible} playing=${snap.playing} openWs=${concurrent} traversed=${report.camerasTraversed.size}`,
      );
      if (scrollCycles % 40 === 0) {
        await healthTick(`scroll-${scrollCycles}`);
        await memSample(`scroll-${scrollCycles}`);
      }
    }
  }
  // settle after scroll
  await page.evaluate(() => {
    const el = document.querySelector('[data-live-grid-cols]');
    if (el) el.scrollTop = 0;
  });
  await sleep(8000);
  const afterScroll = await gridSnap();
  report.phases.scroll = {
    cycles: scrollCycles,
    stepEvents: positions,
    uniqueIpsSeen: report.camerasTraversed.size,
    afterSettle: afterScroll,
    openWsAfterSettle: [...openBySrc.values()].reduce((a, b) => a + b, 0),
  };
  await memSample('after-scroll');
  await healthTick('after-scroll');

  // Visit additional large groups to push camera positions toward 200–300
  for (const extra of [GROUPS.w2, GROUPS.w3, GROUPS.mixed]) {
    const snap = await openLive(extra, 10000);
    await page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      if (!el) return;
      el.scrollTop = el.scrollHeight;
    });
    await sleep(2500);
    await collectVisibleNames();
    await page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      if (el) el.scrollTop = 0;
    });
    await sleep(2500);
    await collectVisibleNames();
    log(`extra group ${extra} total=${snap.total} traversed=${report.camerasTraversed.size}`);
  }
  report.phases.scroll.uniqueIpsAfterExtraGroups = report.camerasTraversed.size;

  // ===== Phase 3: view-change cycles =====
  log('view-change cycles start');
  const switchGroups = [GROUPS.w1, GROUPS.w2, GROUPS.w3, GROUPS.mixed];
  const switchDeadline = Date.now() + SWITCH_MS;
  const cycles = [];
  let cycle = 0;
  while (Date.now() < switchDeadline && cycle < 24) {
    cycle += 1;
    const group = switchGroups[(cycle - 1) % switchGroups.length];
    const snap = await openLive(group, 7000);
    await page.evaluate(() => {
      const el = document.querySelector('[data-live-grid-cols]');
      if (el) el.scrollTop = Math.min(el.scrollHeight, 900);
    });
    await sleep(1500);
    const tile = page.locator('.group h3').first();
    let fullscreen = null;
    if (await tile.count()) {
      await tile.dblclick().catch(() => {});
      await sleep(3500);
      fullscreen = await page.evaluate(() => {
        const text = document.body.innerText;
        const video = document.querySelector('.fixed video') || document.querySelector('video');
        const vs = document.querySelector('.fixed video-stream') || document.querySelector('video-stream');
        const src = vs?.src || null;
        return {
          status: (text.match(/Playing · .* · go2rtc|Connecting · .* · go2rtc/) || [])[0] || null,
          videoWidth: video?.videoWidth ?? 0,
          src,
          profile: src?.includes('_main') ? 'main' : src?.includes('_sub') ? 'sub' : null,
        };
      });
      await page.keyboard.press('Escape');
      await sleep(2500);
    }
    const back = await gridSnap();
    cycles.push({
      n: cycle,
      group,
      gridPlaying: snap.playing,
      fullscreen,
      backPlaying: back.playing,
      backMounted: back.mounted,
    });
    if (cycle % 5 === 0) {
      await healthTick(`switch-${cycle}`);
      await memSample(`switch-${cycle}`);
      log(`switch cycle ${cycle} fs=${JSON.stringify(fullscreen)} backPlaying=${back.playing}`);
    }
  }
  report.phases.viewChange = { cycles: cycles.length, detail: cycles };

  // PTZ under load — keep 5x5 open
  log('PTZ under 5x5');
  await openLive(GROUPS.mixed, 12000);
  const ptzList = await api(cookie, '/api/ptz/cameras');
  const ptzCam = (ptzList.body?.cameras || []).find((c) => c.online !== false) || ptzList.body?.cameras?.[0];
  let ptzResult = { found: Boolean(ptzCam), camera: ptzCam ? { id: ptzCam.id, name: ptzCam.name, ip: ptzCam.ip_address } : null };
  if (ptzCam?.id) {
    const t0 = Date.now();
    const move = await api(cookie, `/api/ptz/${ptzCam.id}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'up', speed: 2 }),
    });
    await sleep(400);
    const stop = await api(cookie, `/api/ptz/${ptzCam.id}/stop`, { method: 'POST' });
    const tilt = await api(cookie, `/api/ptz/${ptzCam.id}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'left', speed: 2 }),
    });
    await sleep(400);
    await api(cookie, `/api/ptz/${ptzCam.id}/stop`, { method: 'POST' });
    const zoom = await api(cookie, `/api/ptz/${ptzCam.id}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'zoom_in', speed: 2 }),
    });
    await sleep(300);
    await api(cookie, `/api/ptz/${ptzCam.id}/stop`, { method: 'POST' });
    const healthDuring = await api(cookie, '/api/health');
    ptzResult = {
      ...ptzResult,
      moveMs: move.ms,
      moveStatus: move.status,
      moveBody: typeof move.body === 'object' ? move.body : String(move.body).slice(0, 160),
      stopStatus: stop.status,
      tiltStatus: tilt.status,
      zoomStatus: zoom.status,
      healthDuringMs: healthDuring.ms,
      healthDuring: healthDuring.body,
      totalMs: Date.now() - t0,
    };
  }
  report.phases.ptz = ptzResult;
  const gridDuringPtz = await gridSnap();
  report.phases.ptz.gridDuring = {
    playing: gridDuringPtz.playing,
    eligible: gridDuringPtz.eligible,
    mounted: gridDuringPtz.mounted,
  };

  // ===== Phase 4: settled observation =====
  log('settled observation');
  await openLive(GROUPS.w1, 15000);
  const settleStart = Date.now();
  const settleSnaps = [];
  while (Date.now() - settleStart < SETTLE_MS) {
    const snap = await gridSnap();
    settleSnaps.push({
      t: nowIso(),
      playing: snap.playing,
      mounted: snap.mounted,
      eligible: snap.eligible,
      connecting: snap.connecting,
      onlineNoVideo: snap.onlineNoVideo,
      openWs: [...openBySrc.values()].reduce((a, b) => a + b, 0),
    });
    await healthTick('settled');
    await memSample('settled');
    await sleep(30000);
  }
  report.phases.settled = settleSnaps;

  const stAfter = await api(cookie, '/api/go2rtc/status');
  report.workersAfter = (stAfter.body?.workers || []).map((w) => ({
    workerId: w.workerId,
    running: w.running,
    apiPort: w.apiPort,
    assigned: w.assignedCameraCount,
    streams: w.liveStreamCount,
  }));

  await memSample('end');
  await healthTick('end');

  const openNow = [...openBySrc.entries()].filter(([, n]) => n > 0);
  report.ws.openAtEnd = openNow.length;
  report.ws.openSrcSample = openNow.slice(0, 20).map(([src, n]) => ({ src, n }));
  report.ws.legacy = report.ws.legacy;
  report.camerasTraversed = [...report.camerasTraversed];
  report.endedAt = nowIso();
  report.durationSec = Math.round((Date.now() - started) / 1000);

  writeOut(report);
  log(`done duration=${report.durationSec}s traversed=${report.camerasTraversed.length} wsOpened=${report.ws.opened}`);
  console.log(
    JSON.stringify(
      {
        durationSec: report.durationSec,
        traversed: report.camerasTraversed.length,
        ws: report.ws,
        security: report.security,
        workersBefore: report.workersBefore,
        workersAfter: report.workersAfter,
        black: report.black,
        memFirst: report.samples[0],
        memLast: report.samples[report.samples.length - 1],
        ptz: report.phases.ptz && {
          found: report.phases.ptz.found,
          moveStatus: report.phases.ptz.moveStatus,
          healthDuringMs: report.phases.ptz.healthDuringMs,
        },
      },
      null,
      2,
    ),
  );
  await browser.close();
}

main().catch((e) => {
  console.error(e);
  try {
    fs.writeFileSync(
      path.join(ROOT, 'deploy', 'task12b-soak-error.txt'),
      String(e?.stack || e),
    );
  } catch {
    /* ignore */
  }
  process.exit(1);
});
