import { useEffect, useRef } from 'react';
import {
  acquireInstantReplayLease,
  heartbeatInstantReplayLease,
  releaseInstantReplayLease,
} from '../lib/instantReplay';

/**
 * Keep a shared Instant Replay rolling buffer warm for live tiles (including
 * cameras that are also permanently recording). One backend producer per camera;
 * lease TTL covers tab crashes. Sequence/tile swaps release via effect cleanup.
 */
export function useInstantReplayLiveLease(
  camera: { id: string; cameraUid?: string } | null,
  opts: {
    enabled: boolean;
    liveActive: boolean;
  },
): void {
  const leaseIdRef = useRef<string | null>(null);
  const cameraRef = useRef(camera);
  cameraRef.current = camera;

  useEffect(() => {
    if (!opts.enabled || !opts.liveActive || !camera) {
      return undefined;
    }

    let cancelled = false;
    let timer: number | undefined;

    (async () => {
      const lease = await acquireInstantReplayLease(camera);
      if (cancelled || !lease.ok || !lease.leaseId) return;
      leaseIdRef.current = lease.leaseId;
      timer = window.setInterval(() => {
        const lid = leaseIdRef.current;
        const cam = cameraRef.current;
        if (!lid || !cam) return;
        void heartbeatInstantReplayLease(cam, lid);
      }, 25000);
    })();

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
      const lid = leaseIdRef.current;
      leaseIdRef.current = null;
      const cam = cameraRef.current;
      if (lid && cam) void releaseInstantReplayLease(cam, lid);
    };
  }, [camera?.id, camera?.cameraUid, opts.enabled, opts.liveActive]);
}
