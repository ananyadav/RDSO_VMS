import { apiFetch } from './api';

/** Matches backend DEFAULT_APP_TIMEZONE when client-config is unavailable. */
export const DEFAULT_APP_TIMEZONE = 'Asia/Kolkata';

let cachedTimezone: string | null = null;
let loadPromise: Promise<string> | null = null;

export function getCachedAppTimezone(): string {
  return cachedTimezone ?? DEFAULT_APP_TIMEZONE;
}

export function setAppTimezoneForTests(tz: string | null): void {
  cachedTimezone = tz;
  loadPromise = null;
}

function partsMap(dt: Date, timeZone: string): Record<string, string> {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const out: Record<string, string> = {};
  for (const part of fmt.formatToParts(dt)) {
    if (part.type !== 'literal') out[part.type] = part.value;
  }
  return out;
}

/**
 * UTC epoch ms for wall-clock Y-M-D H:M:S in the given IANA timezone.
 * Iteratively corrects for DST / fixed offsets via Intl.
 */
export function zonedWallTimeToUtcMs(
  dateKey: string,
  timeZone: string,
  hour = 0,
  minute = 0,
  second = 0,
): number {
  const [y, m, d] = dateKey.split('-').map(Number);
  let utc = Date.UTC(y, m - 1, d, hour, minute, second);
  for (let i = 0; i < 4; i += 1) {
    const p = partsMap(new Date(utc), timeZone);
    const asLocal = Date.UTC(
      Number(p.year),
      Number(p.month) - 1,
      Number(p.day),
      Number(p.hour) === 24 ? 0 : Number(p.hour),
      Number(p.minute),
      Number(p.second),
    );
    const desired = Date.UTC(y, m - 1, d, hour, minute, second);
    const delta = desired - asLocal;
    if (delta === 0) break;
    utc += delta;
  }
  return utc;
}

/** Half-open site-calendar day [startMs, endMs) for YYYY-MM-DD in timeZone. */
export function siteDayBoundsMs(
  dateKey: string,
  timeZone: string = getCachedAppTimezone(),
): { startMs: number; endMs: number } {
  const startMs = zonedWallTimeToUtcMs(dateKey, timeZone, 0, 0, 0);
  const [y, m, d] = dateKey.split('-').map(Number);
  const next = new Date(Date.UTC(y, m - 1, d + 1));
  const nextKey = `${next.getUTCFullYear()}-${String(next.getUTCMonth() + 1).padStart(2, '0')}-${String(next.getUTCDate()).padStart(2, '0')}`;
  const endMs = zonedWallTimeToUtcMs(nextKey, timeZone, 0, 0, 0);
  return { startMs, endMs };
}

/** Calendar YYYY-MM-DD from a Date using its local Y/M/D components (Playback calendar). */
export function calendarDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

export function formatInAppTimezone(
  isoOrMs: string | number | Date,
  options: Intl.DateTimeFormatOptions,
  timeZone: string = getCachedAppTimezone(),
): string {
  try {
    const d =
      isoOrMs instanceof Date
        ? isoOrMs
        : typeof isoOrMs === 'number'
          ? new Date(isoOrMs)
          : new Date(isoOrMs);
    if (Number.isNaN(d.getTime())) return String(isoOrMs);
    return new Intl.DateTimeFormat(undefined, { ...options, timeZone }).format(d);
  } catch {
    return String(isoOrMs);
  }
}

export function formatPlaybackClock(
  isoOrMs: string | number | Date,
  timeZone: string = getCachedAppTimezone(),
): string {
  return formatInAppTimezone(
    isoOrMs,
    {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    },
    timeZone,
  );
}

export function formatPlaybackDate(
  isoOrMs: string | number | Date,
  timeZone: string = getCachedAppTimezone(),
): string {
  return formatInAppTimezone(
    isoOrMs,
    {
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
    },
    timeZone,
  );
}

export function formatPlaybackDateLong(
  dateKey: string,
  timeZone: string = getCachedAppTimezone(),
): string {
  const startMs = zonedWallTimeToUtcMs(dateKey, timeZone, 12, 0, 0);
  return formatInAppTimezone(
    startMs,
    {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    },
    timeZone,
  );
}

/** Load APP_TIMEZONE from backend; caches result. Falls back to Asia/Kolkata. */
export async function ensureAppTimezone(): Promise<string> {
  if (cachedTimezone) return cachedTimezone;
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    try {
      const res = await apiFetch('/api/playback/client-config');
      if (res.ok) {
        const data = (await res.json()) as { timezone?: string };
        const tz = (data.timezone || '').trim();
        if (tz) {
          cachedTimezone = tz;
          return cachedTimezone;
        }
      }
    } catch {
      // fall through
    }
    cachedTimezone = DEFAULT_APP_TIMEZONE;
    return cachedTimezone;
  })();
  try {
    return await loadPromise;
  } finally {
    loadPromise = null;
  }
}
