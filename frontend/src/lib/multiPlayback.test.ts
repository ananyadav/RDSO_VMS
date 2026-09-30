import { describe, expect, it } from 'vitest';
import {
  MAX_MULTI_PLAYBACK_CAMERAS,
  NO_RECORDING_AT_TIME,
  buildMultiSearchQuery,
  multiGridCols,
  parseTimeOfDay,
  resolveRecordingAtTime,
  wallClockAfterDelta,
} from './multiPlayback';

const recA = {
  sessionId: 's1',
  startTime: '2026-06-08T10:00:00.000Z',
  endTime: '2026-06-08T11:00:00.000Z',
  duration: 3600,
  playlistUrl: '/api/playback/cam1/s1/media/index.m3u8',
  playable: true,
};

const recB = {
  sessionId: 's2',
  startTime: '2026-06-08T12:00:00.000Z',
  endTime: '2026-06-08T12:30:00.000Z',
  duration: 1800,
  playlistUrl: '/api/playback/cam2/s2/media/index.m3u8',
  playable: true,
};

describe('multiPlayback helpers', () => {
  it('parses time of day', () => {
    expect(parseTimeOfDay('09:30')).toEqual({ hour: 9, minute: 30, second: 0 });
    expect(parseTimeOfDay('9:05:07')).toEqual({ hour: 9, minute: 5, second: 7 });
    expect(parseTimeOfDay('25:00')).toBeNull();
  });

  it('resolves recording at shared clock time', () => {
    const hit = resolveRecordingAtTime([recA, recB], Date.parse('2026-06-08T10:15:00.000Z'));
    expect(hit.ok).toBe(true);
    expect(hit.sessionId).toBe('s1');
    expect(hit.offsetSeconds).toBe(900);
  });

  it('returns no_footage when time is outside all sessions', () => {
    const miss = resolveRecordingAtTime([recA], Date.parse('2026-06-08T11:30:00.000Z'));
    expect(miss.ok).toBe(false);
    expect(miss.code).toBe('no_footage');
    expect(miss.error).toBe(NO_RECORDING_AT_TIME);
  });

  it('builds multi-search query and grid columns', () => {
    const q = buildMultiSearchQuery({
      date: '2026-06-08',
      cameraRefs: ['cam_a', 'cam_b'],
      atIso: '2026-06-08T10:00:00.000Z',
    });
    expect(q).toContain('date=2026-06-08');
    expect(q).toContain('cameraUid=cam_a');
    expect(q).toContain('cameraUid=cam_b');
    expect(q).toContain('at=');
    expect(multiGridCols(1)).toBe(1);
    expect(multiGridCols(2)).toBe(2);
    expect(multiGridCols(4)).toBe(2);
    expect(multiGridCols(9)).toBe(3);
    expect(multiGridCols(16)).toBe(4);
    expect(MAX_MULTI_PLAYBACK_CAMERAS).toBe(16);
  });

  it('advances shared wall clock', () => {
    expect(wallClockAfterDelta('2026-06-08T10:00:00.000Z', 10)).toBe(
      '2026-06-08T10:00:10.000Z',
    );
  });
});
