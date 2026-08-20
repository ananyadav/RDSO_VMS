/** Load go2rtc video-stream custom element (WebRTC + MSE). */

import {
  destroyLiveMonitorVideo,
  LIVE_MONITOR_PLAYER_CLASS,
  watchGo2RtcVideo,
} from './liveMonitorVideo';
import type { LiveLatencySession } from './liveLatencyMetrics';
import { buildGo2RtcStreamSrc, normalizeWorkerId } from './mediaUrls';

const GO2RTC_PLAYER_MODULE = '/go2rtc/video-stream.js';
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

async function loadGo2RtcModule(): Promise<void> {
  if (isPlayerRegistered()) return;

  const existing = document.querySelector(`script[data-go2rtc="${GO2RTC_PLAYER_MODULE}"]`);
  if (existing) {
    if (!(await waitForPlayerRegistration(REGISTER_WAIT_MS))) {
      throw new Error('go2rtc player script loaded but video-stream element never registered');
    }
    return;
  }

  // Load the module once via <script type="module">.
  // Do NOT preflight-fetch the JS bodies first — that doubled download cost
  // (~1.3s through Nginx→Python→go2rtc) and sat on the T2→T3 path.
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.type = 'module';
    script.src = GO2RTC_PLAYER_MODULE;
    script.dataset.go2rtc = GO2RTC_PLAYER_MODULE;
    script.onload = () => {
      void waitForPlayerRegistration(REGISTER_WAIT_MS).then((ok) => {
        if (ok) resolve();
        else reject(new Error('Failed to register go2rtc video-stream custom element'));
      });
    };
    script.onerror = () =>
      reject(
        new Error(
          `Failed to load ${GO2RTC_PLAYER_MODULE} (check session / go2rtc Diagnostics)`,
        ),
      );
    document.head.appendChild(script);
  });
}

export function ensureGo2RtcPlayer(): Promise<void> {
  if (isPlayerRegistered()) {
    return Promise.resolve();
  }
  if (loadPromise) return loadPromise;

  loadPromise = loadGo2RtcModule().catch((err) => {
    loadPromise = null;
    throw err;
  });

  return loadPromise;
}

export interface Go2RtcMountOptions {
  stream: string;
  mode: 'webrtc' | 'mse';
  workerId?: number | string | null;
  /**
   * Keep the WebSocket up if the pane briefly has 0 height (PTZ layout).
   * Teardown still calls ondisconnect() in destroyGo2RtcPlayerElement.
   */
  background?: boolean;
  onFirstFrame?: (ms: number) => void;
  onModeLabel?: (label: string) => void;
  onError?: (message: string) => void;
  /** Dev-only latency instrumentation (Task 6). */
  latencySession?: LiveLatencySession | null;
}

function attachVideoLatencyHooks(
  video: HTMLVideoElement,
  session: LiveLatencySession | null | undefined,
  onFirstFrame: () => void,
): () => void {
  if (!session) {
    return () => {};
  }

  let rvfcId: number | null = null;
  const cleanups: Array<() => void> = [];

  const markT7Once = () => {
    if (session.t7 != null) return;
    session.markT7();
    onFirstFrame();
  };

  const tryMarkT4 = () => {
    if (session.t4 != null) return;
    const stream = video.srcObject;
    if (stream instanceof MediaStream && stream.getVideoTracks().some((t) => t.readyState === 'live')) {
      session.markT4('srcObject-live');
      return;
    }
    if (video.networkState === HTMLMediaElement.NETWORK_LOADING && video.readyState >= HTMLMediaElement.HAVE_METADATA) {
      session.markT4('media-loading');
    }
  };

  const onLoadStart = () => {
    if (session.t4 == null) session.markT4('loadstart');
  };
  const onLoadedMetadata = () => session.markT5();
  const onPlaying = () => session.markT6();

  video.addEventListener('loadstart', onLoadStart, { once: true });
  video.addEventListener('loadedmetadata', onLoadedMetadata, { once: true });
  video.addEventListener('playing', onPlaying, { once: true });
  cleanups.push(() => {
    video.removeEventListener('loadstart', onLoadStart);
    video.removeEventListener('loadedmetadata', onLoadedMetadata);
    video.removeEventListener('playing', onPlaying);
  });

  if (typeof video.requestVideoFrameCallback === 'function') {
    rvfcId = video.requestVideoFrameCallback(() => {
      if (video.videoWidth > 0) markT7Once();
    });
    cleanups.push(() => {
      if (rvfcId != null && typeof video.cancelVideoFrameCallback === 'function') {
        try {
          video.cancelVideoFrameCallback(rvfcId);
        } catch {
          // ignore
        }
      }
    });
  }

  const poll = window.setInterval(() => {
    tryMarkT4();
    if (session.t7 == null && video.videoWidth > 0 && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      session.markT7();
      onFirstFrame();
      window.clearInterval(poll);
    }
  }, 100);
  cleanups.push(() => window.clearInterval(poll));
  tryMarkT4();

  return () => {
    for (const fn of cleanups) fn();
  };
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
  // T3 = real start of mountGo2RtcPlayer (before any await inside).
  options.latencySession?.markT3();

  await ensureGo2RtcPlayer();
  container.replaceChildren();
  container.classList.add(LIVE_MONITOR_PLAYER_CLASS);

  const t0 = performance.now();
  let reported = false;
  let active = true;
  let detachLatency: (() => void) | null = null;

  const el = document.createElement('video-stream') as VideoStreamEl;

  el.mode = 'mse,webrtc,mjpeg';
  // mode option kept for API compatibility; VideoRTC picks/falls back internally.
  // background=true skips VideoRTC's own DOM teardown — destroyGo2RtcPlayerElement
  // still calls ondisconnect(), so PTZ can survive a 0-height layout pass.
  el.background = options.background === true;
  el.visibilityCheck = false;
  el.visibilityThreshold = 0;
  el.style.display = 'block';
  el.style.width = '100%';
  el.style.height = '100%';
  const workerId = normalizeWorkerId(options.workerId);
  let src: string;
  try {
    src = buildGo2RtcStreamSrc(options.stream, workerId);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Direct media route unavailable';
    console.error(`[live-media] ${msg}`);
    options.onError?.(msg);
    throw err;
  }
  el.src = src;

  if (import.meta.env.DEV) {
    console.info(
      `[live-media]\ncamera=${options.stream}\nworker=${workerId ?? 'n/a'}\nmode=direct\nurl=${src}`,
    );
  }

  const reportFrame = () => {
    if (reported) return;
    const video = el.video;
    if (!video || video.videoWidth === 0) return;
    reported = true;
    if (options.latencySession?.t7 == null) {
      options.latencySession?.markT7();
    }
    options.onFirstFrame?.(Math.round(performance.now() - t0));
  };

  const poll = options.latencySession
    ? null
    : window.setInterval(() => {
        reportFrame();
        if (reported) window.clearInterval(poll!);
      }, 100);

  const modeObserver = window.setInterval(() => {
    if (!active) return;
    const modeEl = el.querySelector('.mode');
    const modeLabel = modeEl?.textContent?.trim();
    if (modeLabel && modeLabel !== 'loading' && modeLabel !== 'error') {
      options.onModeLabel?.(modeLabel);
    }
    if (modeLabel === 'error') {
      const err = el.querySelector('.status')?.textContent?.trim();
      if (err) options.onError?.(err);
    }
  }, 500);

  const onElError = () => options.onError?.('Player error');
  el.addEventListener('error', onElError, { once: true });

  container.appendChild(el);

  const unwatchVideo = watchGo2RtcVideo(container, () => active, (video) => {
    detachLatency?.();
    detachLatency = attachVideoLatencyHooks(video, options.latencySession, reportFrame);
  });

  const cleanup = () => {
    active = false;
    if (poll != null) window.clearInterval(poll);
    detachLatency?.();
    detachLatency = null;
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
