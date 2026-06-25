import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

export function usePlaybackDates(
  cameraId: string | null,
  cameraUid: string | null | undefined,
  year: number,
  month: number,
): { dates: Set<string>; loading: boolean } {
  const [dates, setDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!cameraId && !cameraUid) {
      setDates(new Set());
      return;
    }

    const ac = new AbortController();
    setLoading(true);

    const params = new URLSearchParams({
      year: String(year),
      month: String(month),
    });
    if (cameraUid) {
      params.set('cameraUid', cameraUid);
    } else if (cameraId) {
      params.set('cameraId', cameraId);
    }

    apiFetch(`/api/playback/dates?${params}`, { signal: ac.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => setDates(new Set(data.dates || [])))
      .catch(() => {
        if (!ac.signal.aborted) setDates(new Set());
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });

    return () => ac.abort();
  }, [cameraId, cameraUid, year, month]);

  return { dates, loading };
}
