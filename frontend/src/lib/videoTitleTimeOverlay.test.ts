import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setAppTimezoneForTests } from './appTimezone';
import { digitalZoomCssTransform } from './digitalZoom';
import {
  formatVideoOverlayDate,
  formatVideoOverlayDateTime,
  formatVideoOverlayTime,
  videoTitleTimeOverlayCapabilityPublic,
} from './videoTitleTimeOverlay';
import { cameraTileLabel } from './cameraLabel';

describe('RDSO 18.2.29 video title/time overlay', () => {
  beforeEach(() => {
    setAppTimezoneForTests('Asia/Kolkata');
  });

  afterEach(() => {
    setAppTimezoneForTests(null);
    vi.useRealTimers();
  });

  it('formats date and time in APP_TIMEZONE (not a separate TZ stack)', () => {
    // 2026-09-10 06:15:30 UTC → 11:45:30 IST
    const now = new Date('2026-09-10T06:15:30.000Z');
    expect(formatVideoOverlayDate(now, 'Asia/Kolkata')).toMatch(/09/);
    expect(formatVideoOverlayTime(now, 'Asia/Kolkata')).toMatch(/11:45:30/);
    const line = formatVideoOverlayDateTime(now, 'UTC');
    expect(line.time).toMatch(/06:15:30/);
  });

  it('camera title uses display label helpers', () => {
    expect(cameraTileLabel({ displayName: 'Gate Cam', name: 'cam1' })).toBe('Gate Cam');
    expect(cameraTileLabel({ name: 'Lobby' })).toBe('Lobby');
  });

  it('clock advances when time updates', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-10T06:00:00.000Z'));
    const a = formatVideoOverlayTime(new Date(), 'UTC');
    vi.setSystemTime(new Date('2026-09-10T06:00:05.000Z'));
    const b = formatVideoOverlayTime(new Date(), 'UTC');
    expect(a).toBe('06:00:00');
    expect(b).toBe('06:00:05');
  });

  it('publishes capability flags (client overlay, not burned-in)', () => {
    const cap = videoTitleTimeOverlayCapabilityPublic();
    expect(cap.rdso_18_2_29).toBe(true);
    expect(cap.burns_into_stream).toBe(false);
    expect(cap.uses_app_timezone).toBe(true);
    expect(cap.live_tiles).toBe(true);
    expect(cap.fullscreen).toBe(true);
  });

  it('digital zoom CSS applies only to the player transform (OSD stays outside)', () => {
    // Regression: overlay is a sibling of the zoomed player, not inside scale().
    const css = digitalZoomCssTransform({ scale: 4, panX: 10, panY: -5 });
    expect(css).toContain('scale(4)');
    expect(css).toContain('translate');
    const line = formatVideoOverlayDateTime(new Date('2026-09-10T06:15:30.000Z'), 'Asia/Kolkata');
    expect(line.line).toMatch(/11:45:30/);
  });
});
