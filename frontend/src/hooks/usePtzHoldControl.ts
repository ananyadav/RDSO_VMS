import { useCallback, useEffect, useRef } from 'react';
import toast from 'react-hot-toast';
import { ptzMove, ptzStop } from '../lib/ptzApi';

interface UsePtzHoldControlOptions {
  /** When false, move/stop no-ops (except cleanup still stops). */
  enabled?: boolean;
  /** Camera must be online for move; stop still runs. */
  online?: boolean;
  speed?: number;
}

/**
 * Hold-to-move PTZ control with release/unmount/camera-swap stop.
 * Reuses /api/ptz/{id}/move and /stop — does not open presets/tours.
 */
export function usePtzHoldControl(
  cameraId: string | null | undefined,
  options: UsePtzHoldControlOptions = {},
) {
  const { enabled = true, online = true, speed = 2 } = options;
  const moveTokenRef = useRef(0);
  const cameraIdRef = useRef(cameraId);
  cameraIdRef.current = cameraId;

  const stopForCamera = useCallback(async (id: string | null | undefined) => {
    if (!id) return;
    moveTokenRef.current += 1;
    try {
      await ptzStop(id);
    } catch {
      // Best-effort stop on release/unmount.
    }
  }, []);

  // Stop previous camera when the controlled id changes; stop on unmount.
  useEffect(() => {
    const id = cameraId;
    return () => {
      void stopForCamera(id);
    };
  }, [cameraId, stopForCamera]);

  const onMoveStart = useCallback(
    async (direction: string) => {
      const id = cameraIdRef.current;
      if (!id || !enabled || !online) return;
      const token = ++moveTokenRef.current;
      if (direction === 'home') {
        await stopForCamera(id);
        return;
      }
      const result = await ptzMove(id, direction, speed);
      if (token !== moveTokenRef.current) return;
      if (!result.ok) {
        toast.error(result.error || 'PTZ move failed');
      }
    },
    [enabled, online, speed, stopForCamera],
  );

  const onMoveStop = useCallback(async () => {
    await stopForCamera(cameraIdRef.current);
  }, [stopForCamera]);

  return {
    onMoveStart,
    onMoveStop,
    disabled: !enabled || !online || !cameraId,
  };
}
