/** Load go2rtc video-stream custom element (WebRTC + MSE). */

import { apiFetch } from './api';
import {
  destroyLiveMonitorVideo,
  LIVE_MONITOR_PLAYER_CLASS,
  watchGo2RtcVideo,
} from './liveMonitorVideo';
import { authService } from '../services/authService';

const GO2RTC_PLAYER_MODULE = '/go2rtc/video-stream.js';
const GO2RTC_RTC_MODULE = '/go2rtc/video-rtc.js';
const REGISTER_WAIT_MS = 15_000;

let loadPromise: Promise<void> | null = null;

/** Active player teardown callbacks (page unmount / navigation). */
const activeCleanups = new Set<() => void>();

export function destroyAllGo2RtcPlayers(): void {
  for (const fn of [...activeCleanups]) {
    try {
      fn();
    } catch {
      // ignore
    }
  }
  activeCleanups.clear();
}

function isPlayerRegistered(): boolean {
  return Boolean(customElements.get('video-stream'));
}

async function waitForPlayerRegistration(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (isPlayerRegistered()) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return isPlayerRegistered();
}

async function preflightGo2RtcAssets(): Promise<void> {
  for (const path of [GO2RTC_PLAYER_MODULE, GO2RTC_RTC_MODULE]) {
    const res = await apiFetch(path, { headers: { Accept: 'text/javascript,*/*' } });
    if (!res.ok) {
      const hint =
        res.status === 401
          ? 'Session expired — log in again'
          : res.status === 502 || res.status === 503
            ? 'Backend cannot reach go2rtc — open go2rtc Diagnostics and click Start'
            : `HTTP ${res.status}`;
      throw new Error(`Failed to load ${path} (${hint})`);
    }
  }
}

export function ensureGo2RtcPlayer(): Promise<void> {
  if (isPlayerRegistered()) {
    return Promise.resolve();
  }
  if (loadPromise) return loadPromise;

  loadPromise = (async () => {
    // index.html preloads the module; wait for custom element registration first.
    if (await waitForPlayerRegistration(2_000)) return;

    await preflightGo2RtcAssets();

    // No query string — relative import ./video-rtc.js must resolve under /go2rtc/.
    await import(/* @vite-ignore */ GO2RTC_PLAYER_MODULE);

    if (await waitForPlayerRegistration(REGISTER_WAIT_MS)) return;

    throw new Error(
      'Failed to load go2rtc player — check /go2rtc/video-stream.js and /go2rtc/video-rtc.js',
    );
  })().catch((err) => {
    loadPromise = null;
    throw err;
  });

  return loadPromise;
}

export interface Go2RtcMountOptions {
  stream: string;
  mode: 'webrtc' | 'mse';
  onFirstFrame?: (ms: number) => void;
  onModeLabel?: (label: string) => void;
  onError?: (message: string) => void;
}

/** go2rtc VideoRTC internals — background=true skips DOM disconnect teardown. */
type VideoStreamEl = HTMLElement & {
  mode: string;
  src: string;
  background: boolean;
  visibilityCheck: boolean;
  visibilityThreshold: number;
  video: HTMLVideoElement | null;
  ondisconnect?: () => void;
  disconnectTID?: number;
  reconnectTID?: number;
};

/**
 * Tear down go2rtc video-stream immediately.
 * Must call ondisconnect() — with background=true, remove() alone leaks consumers.
 */
export function destroyGo2RtcPlayerElement(el: VideoStreamEl | null): void {
  if (!el) return;
  try {
    if (el.disconnectTID) {
      clearTimeout(el.disconnectTID);
      el.disconnectTID = 0;
    }
    if (el.reconnectTID) {
      clearTimeout(el.reconnectTID);
      el.reconnectTID = 0;
    }
    if (typeof el.ondisconnect === 'function') {
      el.ondisconnect();
    }
  } catch {
    // ignore
  }
  destroyLiveMonitorVideo(el.video);
  try {
    el.src = '';
  } catch {
    // ignore
  }
  try {
    el.remove();
  } catch {
    // ignore
  }
}

export async function mountGo2RtcPlayer(
  container: HTMLElement,
  options: Go2RtcMountOptions,
): Promise<() => void> {
  await ensureGo2RtcPlayer();
  container.replaceChildren();
  container.classList.add(LIVE_MONITOR_PLAYER_CLASS);

  const t0 = performance.now();
  let reported = false;
  let active = true;

  const el = document.createElement('video-stream') as VideoStreamEl;

  el.mode = options.mode;
  // We manage lifecycle in useGo2RtcLive — never use background=true (it blocks DOM teardown).
  el.background = false;
  el.visibilityCheck = false;
  el.visibilityThreshold = 0;
  el.style.display = 'block';
  el.style.width = '100%';
  el.style.height = '100%';
  const userId = authService.getUserId();
  const wsBase = `/go2rtc/api/ws?src=${encodeURIComponent(options.stream)}`;
  el.src = userId ? `${wsBase}&uid=${encodeURIComponent(userId)}` : wsBase;

  const reportFrame = () => {
    if (reported) return;
    const video = el.video;
    if (!video || video.videoWidth === 0) return;
    reported = true;
    options.onFirstFrame?.(Math.round(performance.now() - t0));
  };

  const poll = window.setInterval(() => {
    reportFrame();
    if (reported) window.clearInterval(poll);
  }, 100);

  const modeObserver = window.setInterval(() => {
    if (!active) return;
    const modeEl = el.querySelector('.mode');
    const label = modeEl?.textContent?.trim();
    if (label && label !== 'loading') {
      options.onModeLabel?.(label);
    }
    const errEl = el.querySelector('.status');
    const err = errEl?.textContent?.trim();
    if (err) options.onError?.(err);
  }, 500);

  const onElError = () => options.onError?.('Player error');
  el.addEventListener('error', onElError, { once: true });

  container.appendChild(el);

  const unwatchVideo = watchGo2RtcVideo(container, () => active, () => reportFrame());

  const cleanup = () => {
    active = false;
    window.clearInterval(poll);
    window.clearInterval(modeObserver);
    el.removeEventListener('error', onElError);
    unwatchVideo();
    destroyGo2RtcPlayerElement(el);
    container.replaceChildren();
    container.classList.remove(LIVE_MONITOR_PLAYER_CLASS);
    activeCleanups.delete(cleanup);
  };

  activeCleanups.add(cleanup);
  return cleanup;
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => destroyAllGo2RtcPlayers());
}
