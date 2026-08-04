import { useEffect, useRef } from 'react';

/**
 * Run `callback` on an interval while the browser tab is visible.
 * Pauses when the tab is hidden; fires once immediately when it becomes visible again.
 */
export function useVisibilityInterval(
  callback: () => void,
  delayMs: number,
  enabled = true,
): void {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!enabled || delayMs <= 0) return;

    let intervalId: number | null = null;

    const tick = () => {
      if (!document.hidden) callbackRef.current();
    };

    const start = () => {
      if (intervalId != null) return;
      intervalId = window.setInterval(tick, delayMs);
    };

    const stop = () => {
      if (intervalId == null) return;
      window.clearInterval(intervalId);
      intervalId = null;
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
        return;
      }
      tick();
      start();
    };

    if (!document.hidden) {
      start();
    }
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [delayMs, enabled]);
}
