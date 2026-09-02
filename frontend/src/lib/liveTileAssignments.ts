/** Live View per-tile assignments (RDSO 18.1.10 + 18.1.11.2). */

export type SlotAssignment =
  | { kind: 'camera'; id: string }
  | { kind: 'sequence'; id: string }
  | null;

export type SlotAssignments = SlotAssignment[];

export const LIVE_CAMERA_DRAG_MIME = 'application/x-live-camera-id';
export const LIVE_CAMERA_SEQUENCE_DRAG_MIME = 'application/x-live-camera-sequence-id';

export function slotCountForLayout(cameraCount: number, gridCols: number): number {
  const cols = Math.max(1, gridCols);
  const minSlots = cols * cols;
  const rows = Math.max(1, Math.ceil(cameraCount / cols));
  return Math.max(minSlots, rows * cols);
}

export function buildDefaultAssignments(cameraIds: string[], gridCols: number): SlotAssignments {
  const count = slotCountForLayout(cameraIds.length, gridCols);
  const out: SlotAssignments = Array(count).fill(null);
  for (let i = 0; i < cameraIds.length && i < count; i += 1) {
    out[i] = { kind: 'camera', id: cameraIds[i] };
  }
  return out;
}

/** Preserve assignments by slot index when layout changes; extend or trim as needed. */
export function migrateAssignmentsForLayout(
  prev: SlotAssignments,
  _prevCols: number,
  nextCols: number,
  authorizedCameraIds: Set<string>,
  authorizedSequenceIds: Set<string> = new Set(),
): SlotAssignments {
  const preserved: SlotAssignments = prev.map((slot) => {
    if (!slot) return null;
    if (slot.kind === 'camera') {
      return authorizedCameraIds.has(slot.id) ? slot : null;
    }
    return authorizedSequenceIds.has(slot.id) ? slot : null;
  });
  const assignedCount = preserved.filter(Boolean).length;
  const count = slotCountForLayout(assignedCount, nextCols);
  const next: SlotAssignments = Array(count).fill(null);
  for (let i = 0; i < Math.min(preserved.length, count); i += 1) {
    next[i] = preserved[i];
  }
  return next;
}

export function assignCameraToSlot(
  assignments: SlotAssignments,
  slotIndex: number,
  cameraId: string | null,
): SlotAssignments {
  if (slotIndex < 0 || slotIndex >= assignments.length) return assignments;
  const next = [...assignments];
  const assignment: SlotAssignment = cameraId ? { kind: 'camera', id: cameraId } : null;
  if (cameraId) {
    for (let i = 0; i < next.length; i += 1) {
      if (i === slotIndex) continue;
      const slot = next[i];
      if (slot?.kind === 'camera' && slot.id === cameraId) next[i] = null;
    }
  }
  next[slotIndex] = assignment;
  return next;
}

export function assignSequenceToSlot(
  assignments: SlotAssignments,
  slotIndex: number,
  sequenceId: string | null,
): SlotAssignments {
  if (slotIndex < 0 || slotIndex >= assignments.length) return assignments;
  const next = [...assignments];
  const assignment: SlotAssignment = sequenceId ? { kind: 'sequence', id: sequenceId } : null;
  if (sequenceId) {
    for (let i = 0; i < next.length; i += 1) {
      if (i === slotIndex) continue;
      const slot = next[i];
      if (slot?.kind === 'sequence' && slot.id === sequenceId) next[i] = null;
    }
  }
  next[slotIndex] = assignment;
  return next;
}

export function assignedCameraIds(assignments: SlotAssignments): Set<string> {
  const out = new Set<string>();
  for (const slot of assignments) {
    if (slot?.kind === 'camera') out.add(slot.id);
  }
  return out;
}

export function assignedSequenceIds(assignments: SlotAssignments): Set<string> {
  const out = new Set<string>();
  for (const slot of assignments) {
    if (slot?.kind === 'sequence') out.add(slot.id);
  }
  return out;
}

/** @deprecated use assignedCameraIds — kept for imports during migration */
export type LegacySlotAssignments = (string | null)[];
