import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import {
  DEFAULT_APP_TIMEZONE,
  calendarDateKey,
  formatPlaybackClock,
  setAppTimezoneForTests,
  siteDayBoundsMs,
  zonedWallTimeToUtcMs,
} from './appTimezone';

describe('appTimezone playback display', () => {
  beforeEach(() => {
    setAppTimezoneForTests(null);
  });

  afterEach(() => {
    setAppTimezoneForTests(null);
    vi.unstubAllGlobals();
  });

  it('A. Asia/Kolkata shows IST clock for a UTC timestamp', () => {
    // 2026-09-04 00:30 IST = 2026-09-03 19:00 UTC
    const utcIso = '2026-09-03T19:00:00.000Z';
    expect(formatPlaybackClock(utcIso, 'Asia/Kolkata')).toBe('00:30:00');
  });

  it('B. display follows application timezone, not a mismatched browser locale alone', () => {
    const utcIso = '2026-09-03T19:00:00.000Z';
    // Same instant: site IST vs UTC config must differ
    expect(formatPlaybackClock(utcIso, 'Asia/Kolkata')).toBe('00:30:00');
    expect(formatPlaybackClock(utcIso, 'UTC')).toBe('19:00:00');
    expect(formatPlaybackClock(utcIso, 'America/New_York')).not.toBe(
      formatPlaybackClock(utcIso, 'Asia/Kolkata'),
    );
  });

  it('C. UTC configuration displays UTC clock', () => {
    const utcIso = '2026-09-04T12:00:00.000Z';
    expect(formatPlaybackClock(utcIso, 'UTC')).toBe('12:00:00');
  });

  it('site day bounds for Kolkata match backend half-open interval', () => {
    const { startMs, endMs } = siteDayBoundsMs('2026-09-04', 'Asia/Kolkata');
    expect(new Date(startMs).toISOString()).toBe('2026-09-03T18:30:00.000Z');
    expect(new Date(endMs).toISOString()).toBe('2026-09-04T18:30:00.000Z');
  });

  it('UTC day bounds are midnight-to-midnight UTC', () => {
    const { startMs, endMs } = siteDayBoundsMs('2026-09-04', 'UTC');
    expect(new Date(startMs).toISOString()).toBe('2026-09-04T00:00:00.000Z');
    expect(new Date(endMs).toISOString()).toBe('2026-09-05T00:00:00.000Z');
  });

  it('zonedWallTimeToUtcMs noon Kolkata', () => {
    const ms = zonedWallTimeToUtcMs('2026-09-04', 'Asia/Kolkata', 12, 0, 0);
    expect(new Date(ms).toISOString()).toBe('2026-09-04T06:30:00.000Z');
  });

  it('default fallback constant is Asia/Kolkata', () => {
    expect(DEFAULT_APP_TIMEZONE).toBe('Asia/Kolkata');
  });

  it('calendarDateKey uses local Y-M-D components', () => {
    const d = new Date(2026, 8, 4); // Sep 4 local
    expect(calendarDateKey(d)).toBe('2026-09-04');
  });
});
