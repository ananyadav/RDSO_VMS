import { describe, expect, it } from 'vitest';
import {
  canUseInstantReplay,
  enabledGoBackPresets,
  playheadIsoFromOffset,
  timelineFraction,
  timelineClickToIso,
  seekDeltaSeconds,
  cameraRefForApi,
  recentLookbackNeedsBuffer,
} from './instantReplay';

describe('canUseInstantReplay', () => {
  it('allows admin with recording.view path (ops admin)', () => {
    expect(canUseInstantReplay({ role: 'admin', permissions: [] })).toBe(true);
  });

  it('allows operator with recording.view', () => {
    expect(
      canUseInstantReplay({ role: 'operator', permissions: ['recording.view'] }),
    ).toBe(true);
  });

  it('blocks operator without recording.view', () => {
    expect(canUseInstantReplay({ role: 'operator', permissions: ['Live View'] })).toBe(
      false,
    );
  });

  it('blocks null user', () => {
    expect(canUseInstantReplay(null)).toBe(false);
  });
});

describe('enabledGoBackPresets', () => {
  it('enables 10 and 30 when 90s available; disables 5 min', () => {
    const presets = enabledGoBackPresets(90);
    expect(presets.find((p) => p.seconds === 10)?.enabled).toBe(true);
    expect(presets.find((p) => p.seconds === 30)?.enabled).toBe(true);
    expect(presets.find((p) => p.seconds === 60)?.enabled).toBe(true);
    expect(presets.find((p) => p.seconds === 300)?.enabled).toBe(false);
  });

  it('enables 5 min when 300s available', () => {
    expect(enabledGoBackPresets(300).find((p) => p.seconds === 300)?.enabled).toBe(true);
  });

  it('disables all when no history', () => {
    expect(enabledGoBackPresets(0).every((p) => !p.enabled)).toBe(true);
  });
});

describe('recentLookbackNeedsBuffer', () => {
  it('requires buffer for 10/30/60s when permanent segment is 300s', () => {
    expect(recentLookbackNeedsBuffer(10, 300)).toBe(true);
    expect(recentLookbackNeedsBuffer(30, 300)).toBe(true);
    expect(recentLookbackNeedsBuffer(60, 300)).toBe(true);
  });

  it('allows permanent for lookbacks >= permanent segment', () => {
    expect(recentLookbackNeedsBuffer(300, 300)).toBe(false);
    expect(recentLookbackNeedsBuffer(600, 300)).toBe(false);
  });
});

describe('timeline helpers', () => {
  const oldest = '2026-09-04T10:00:00.000Z';
  const newest = '2026-09-04T10:05:00.000Z';

  it('maps offset to playhead ISO', () => {
    expect(playheadIsoFromOffset(oldest, 30)).toBe('2026-09-04T10:00:30.000Z');
  });

  it('computes timeline fraction', () => {
    expect(timelineFraction(oldest, newest, '2026-09-04T10:02:30.000Z')).toBeCloseTo(0.5);
  });

  it('clamps timeline click to window', () => {
    expect(timelineClickToIso(oldest, newest, 0)).toBe(oldest);
    expect(timelineClickToIso(oldest, newest, 1)).toBe(newest);
    expect(timelineClickToIso(oldest, newest, 0.5)).toBe('2026-09-04T10:02:30.000Z');
  });

  it('cannot seek outside coverage (fraction clamped)', () => {
    expect(timelineClickToIso(oldest, newest, -1)).toBe(oldest);
    expect(timelineClickToIso(oldest, newest, 2)).toBe(newest);
  });

  it('seek deltas', () => {
    expect(seekDeltaSeconds('backward', 10)).toBe(-10);
    expect(seekDeltaSeconds('forward', 10)).toBe(10);
  });
});

describe('cameraRefForApi', () => {
  it('prefers cameraUid', () => {
    expect(cameraRefForApi({ id: 'mongo1', cameraUid: 'ip_1_2_3_4' })).toEqual({
      cameraUid: 'ip_1_2_3_4',
    });
  });

  it('falls back to cameraId', () => {
    expect(cameraRefForApi({ id: 'mongo1' })).toEqual({ cameraId: 'mongo1' });
  });
});

describe('Instant Replay action eligibility (UI gate)', () => {
  it('action appears only when recording.view present', () => {
    const eligible = canUseInstantReplay({
      role: 'operator',
      permissions: ['Live View', 'recording.view'],
    });
    const denied = canUseInstantReplay({
      role: 'operator',
      permissions: ['Live View'],
    });
    expect(eligible).toBe(true);
    expect(denied).toBe(false);
  });
});

describe('go-back window cases', () => {
  it('10-sec go-back enabled when history >= 10', () => {
    expect(enabledGoBackPresets(10).find((p) => p.seconds === 10)?.enabled).toBe(true);
  });
  it('30-sec go-back enabled when history >= 30', () => {
    expect(enabledGoBackPresets(30).find((p) => p.seconds === 30)?.enabled).toBe(true);
  });
  it('1-min go-back enabled when history >= 60', () => {
    expect(enabledGoBackPresets(60).find((p) => p.seconds === 60)?.enabled).toBe(true);
  });
  it('5-min go-back when available', () => {
    expect(enabledGoBackPresets(301).find((p) => p.seconds === 300)?.enabled).toBe(true);
  });
  it('unsupported go-back disabled', () => {
    expect(enabledGoBackPresets(45).find((p) => p.seconds === 300)?.enabled).toBe(false);
  });
});

describe('Return to Live / lease identity', () => {
  it('camera ref identity preserved for lease release after tile change', () => {
    const a = cameraRefForApi({ id: 'camA', cameraUid: 'ip_a' });
    const b = cameraRefForApi({ id: 'camB', cameraUid: 'ip_b' });
    expect(a).not.toEqual(b);
    expect(a.cameraUid).toBe('ip_a');
  });
});
