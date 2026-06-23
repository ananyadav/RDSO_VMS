/** Load go2rtc video-stream custom element (WebRTC + MSE). */

import { authService } from '../services/authService';
import {
  destroyLiveMonitorVideo,
  LIVE_MONITOR_PLAYER_CLASS,
  watchGo2RtcVideo,
} from './liveMonitorVideo';

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

export function ensureGo2RtcPlayer(): Promise<void> {
  if (customElements.get('video-stream')) {
    return Promise.resolve();
  }
  if (loadPromise) return loadPromise;

  loadPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.type = 'module';
    script.src = '/go2rtc/video-stream.js';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load /go2rtc/video-stream.js — is go2rtc running?'));
    document.head.appendChild(script);
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
