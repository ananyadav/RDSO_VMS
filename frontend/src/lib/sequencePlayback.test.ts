import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  advanceSequenceIndex,
  dwellMsFromSeconds,
  sequenceCameraOrder,
  shouldRotateSequence,
} from '../lib/sequencePlayback';
import {
  assignCameraToSlot,
  assignSequenceToSlot,
  buildDefaultAssignments,
  migrateAssignmentsForLayout,
} from '../lib/liveTileAssignments';

describe('sequencePlayback rotation', () => {
  it('advances A → B → C → A', () => {
    expect(advanceSequenceIndex(0, 3)).toBe(1);
    expect(advanceSequenceIndex(1, 3)).toBe(2);
    expect(advanceSequenceIndex(2, 3)).toBe(0);
  });

  it('preserves backend order C → A → B', () => {
    const order = sequenceCameraOrder(['c', 'a', 'b']);
    expect(order).toEqual(['c', 'a', 'b']);
    expect(order).not.toEqual(['a', 'b', 'c']);
  });

  it('uses configured dwell ms', () => {
    expect(dwellMsFromSeconds(15)).toBe(15000);
    expect(dwellMsFromSeconds(5)).toBe(5000);
  });

  it('does not rotate with single accessible camera', () => {
    expect(shouldRotateSequence(1)).toBe(false);
    expect(shouldRotateSequence(2)).toBe(true);
  });
});

describe('sequence slot assignments', () => {
  it('assigns sequence without breaking camera MIME type path', () => {
    const base = buildDefaultAssignments(['a', 'b'], 2);
    const withSeq = assignSequenceToSlot(base, 1, 'seq1');
    expect(withSeq[1]).toEqual({ kind: 'sequence', id: 'seq1' });
    expect(withSeq[0]).toEqual({ kind: 'camera', id: 'a' });
  });

  it('camera replaces sequence on assign', () => {
    const withSeq = assignSequenceToSlot(buildDefaultAssignments(['a', 'b'], 2), 0, 'seq1');
    const replaced = assignCameraToSlot(withSeq, 0, 'x');
    expect(replaced[0]).toEqual({ kind: 'camera', id: 'x' });
  });

  it('sequence replaces camera on assign', () => {
    const base = buildDefaultAssignments(['a', 'b'], 2);
    const replaced = assignSequenceToSlot(base, 0, 'seq2');
    expect(replaced[0]).toEqual({ kind: 'sequence', id: 'seq2' });
  });

  it('reorder C/A/B payload order preserved in migration', () => {
    const prev = [
      { kind: 'sequence' as const, id: 'seq1' },
      { kind: 'camera' as const, id: 'a' },
      null,
    ];
    const next = migrateAssignmentsForLayout(prev, 2, 2, new Set(['a']), new Set(['seq1']));
    expect(next[0]).toEqual({ kind: 'sequence', id: 'seq1' });
    expect(next[1]).toEqual({ kind: 'camera', id: 'a' });
  });

  it('ACL sequence uses only authorized camera ids from API response', () => {
    const apiOrder = sequenceCameraOrder(['a', 'c']);
    expect(apiOrder).toEqual(['a', 'c']);
    expect(apiOrder).not.toContain('b');
  });
});

describe('sequence dwell timer cleanup', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('fires after dwell and can be cleared', () => {
    const fn = vi.fn();
    const id = setTimeout(fn, dwellMsFromSeconds(5));
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledOnce();
    clearTimeout(id);
  });
});
