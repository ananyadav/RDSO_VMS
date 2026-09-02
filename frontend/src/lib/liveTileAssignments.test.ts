import { describe, expect, it } from 'vitest';
import {
  assignCameraToSlot,
  assignSequenceToSlot,
  buildDefaultAssignments,
  migrateAssignmentsForLayout,
  slotCountForLayout,
} from '../lib/liveTileAssignments';

describe('liveTileAssignments', () => {
  it('builds default sequential camera assignments', () => {
    const a = buildDefaultAssignments(['c1', 'c2', 'c3'], 2);
    expect(a[0]).toEqual({ kind: 'camera', id: 'c1' });
    expect(a[1]).toEqual({ kind: 'camera', id: 'c2' });
    expect(a[2]).toEqual({ kind: 'camera', id: 'c3' });
    expect(a.length).toBe(slotCountForLayout(3, 2));
  });

  it('moves duplicate camera to new slot on assign', () => {
    const next = assignCameraToSlot(
      [{ kind: 'camera', id: 'a' }, { kind: 'camera', id: 'b' }, null],
      2,
      'a',
    );
    expect(next).toEqual([null, { kind: 'camera', id: 'b' }, { kind: 'camera', id: 'a' }]);
  });

  it('migrates assignments when layout grows', () => {
    const prev = buildDefaultAssignments(['a', 'b', 'c', 'd'], 2);
    const auth = new Set(['a', 'b', 'c', 'd']);
    const next = migrateAssignmentsForLayout(prev, 2, 4, auth);
    expect(next[0]).toEqual({ kind: 'camera', id: 'a' });
    expect(next[1]).toEqual({ kind: 'camera', id: 'b' });
    expect(next[2]).toEqual({ kind: 'camera', id: 'c' });
    expect(next[3]).toEqual({ kind: 'camera', id: 'd' });
    expect(next.length).toBeGreaterThanOrEqual(16);
  });

  it('marks unknown camera ids as empty on migrate', () => {
    const next = migrateAssignmentsForLayout(
      [{ kind: 'camera', id: 'x' }, { kind: 'camera', id: 'y' }],
      2,
      2,
      new Set(['y']),
    );
    expect(next[0]).toBeNull();
    expect(next[1]).toEqual({ kind: 'camera', id: 'y' });
  });

  it('preserves sequence assignment on layout migrate', () => {
    const prev: ReturnType<typeof buildDefaultAssignments> = [
      { kind: 'sequence', id: 's1' },
      { kind: 'camera', id: 'a' },
      null,
      null,
    ];
    const next = migrateAssignmentsForLayout(prev, 2, 4, new Set(['a']), new Set(['s1']));
    expect(next[0]).toEqual({ kind: 'sequence', id: 's1' });
  });

  it('assigns distinct sequence to slot', () => {
    const next = assignSequenceToSlot(
      [{ kind: 'sequence', id: 's1' }, null, null],
      2,
      's2',
    );
    expect(next[2]).toEqual({ kind: 'sequence', id: 's2' });
  });
});
