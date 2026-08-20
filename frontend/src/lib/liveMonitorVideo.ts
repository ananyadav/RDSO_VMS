/** CCTV-style live video — no play/pause UI, auto-resume if paused. */

export const LIVE_MONITOR_PLAYER_CLASS = 'live-monitor-player';

export function applyLiveMonitorVideoAttrs(video: HTMLVideoElement): void {
  video.controls = false;
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  video.setAttribute('playsinline', '');
  video.setAttribute('webkit-playsinline', '');
  video.setAttribute('controlsList', 'nodownload noplaybackrate noremoteplayback');
  video.disablePictureInPicture = true;
  try {
    (video as HTMLVideoElement & { disableRemotePlayback?: boolean }).disableRemotePlayback = true;
  } catch {
    // ignore
  }
}

/** Fully detach a live monitor video element (WebRTC/MSE). */
export function destroyLiveMonitorVideo(video: HTMLVideoElement | null | undefined): void {
  if (!video) return;
  try {
    video.pause();
    const stream = video.srcObject;
    if (stream instanceof MediaStream) {
      stream.getTracks().forEach((t) => t.stop());
    }
    video.srcObject = null;
    video.removeAttribute('src');
    video.load();
  } catch {
    // ignore teardown races
  }
}

/** Attach guards so live feed cannot be paused from the UI. Returns cleanup. */
export function attachLiveMonitorGuards(
  video: HTMLVideoElement,
  isActive: () => boolean = () => true,
): () => void {
  applyLiveMonitorVideoAttrs(video);

  const blockToggle = (e: Event) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const onDblClick = (e: Event) => {
    // Block native video fullscreen / pause, but let Live View toggle the grid overlay.
    e.preventDefault();
  };

  const onPause = () => {
    if (!isActive()) return;
    void video.play().catch(() => {});
  };

  video.addEventListener('click', blockToggle, true);
  video.addEventListener('dblclick', onDblClick, true);
  video.addEventListener('contextmenu', blockToggle, true);
  video.addEventListener('pause', onPause);

  void video.play().catch(() => {});

  return () => {
    video.removeEventListener('click', blockToggle, true);
    video.removeEventListener('dblclick', onDblClick, true);
    video.removeEventListener('contextmenu', blockToggle, true);
    video.removeEventListener('pause', onPause);
    destroyLiveMonitorVideo(video);
  };
}

/** Configure go2rtc video-stream inner video once mounted. */
export function watchGo2RtcVideo(
  root: HTMLElement,
  isActive: () => boolean,
  onVideo?: (video: HTMLVideoElement) => void,
): () => void {
  let detachVideo: (() => void) | null = null;
  let lastVideo: HTMLVideoElement | null = null;

  const bind = () => {
    if (!isActive()) return;
    const video =
      (root.querySelector('video-stream') as { video?: HTMLVideoElement | null } | null)?.video ??
      root.querySelector('video');
    if (!(video instanceof HTMLVideoElement) || video === lastVideo) return;

    detachVideo?.();
    lastVideo = video;
    detachVideo = attachLiveMonitorGuards(video, isActive);
    onVideo?.(video);
  };

  const poll = window.setInterval(bind, 100);
  bind();

  const observer = new MutationObserver(bind);
  observer.observe(root, { childList: true, subtree: true });

  return () => {
    window.clearInterval(poll);
    observer.disconnect();
    detachVideo?.();
    detachVideo = null;
    lastVideo = null;
  };
}
