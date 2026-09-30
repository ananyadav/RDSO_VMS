/** RDSO 18.2.28 — alarm-driven Live View layout/slot switching (pure).

Distinct from 18.1.25 fullscreen auto-display: when a rule carries display_switch,
the matching monitor applies layout + camera slot and can restore prior state.
*/

import type { AlarmEvent } from './eventsApi';
import type { SlotAssignments } from './liveTileAssignments';
import {
  assignCameraToSlot,
  buildDefaultAssignments,
  migrateAssignmentsForLayout,
  slotCountForLayout,
} from './liveTileAssignments';
import { LIVE_LAYOUTS, layoutById, type LiveLayoutId } from './liveLayouts';
import { MIN_LIVE_MONITORS, parseMonitorId } from './liveMonitor';

export type AlarmDisplayMode = 'fullscreen' | 'layout_switch';

export interface AlarmDisplaySwitchConfig {
  mode: AlarmDisplayMode;
  monitorId: number | null;
  layout: LiveLayoutId | null;
  slot: number;
  restoreOnReset: boolean;
  cameraId?: string;
}

export interface LiveViewSnapshot {
  layoutLabel: string;
  slots: SlotAssignments;
  eventId: string;
}

const LAYOUT_IDS = new Set(LIVE_LAYOUTS.map((l) => l.id));

export function parseAlarmDisplaySwitch(
  event: AlarmEvent | null | undefined,
): AlarmDisplaySwitchConfig | null {
  if (!event) return null;
  const raw = (event.metadata || {}).display_switch;
  if (!raw || typeof raw !== 'object') return null;
  const ds = raw as Record<string, unknown>;

  const layoutRaw = String(ds.layout || ds.layout_label || '').trim();
  const layout = LAYOUT_IDS.has(layoutRaw as LiveLayoutId)
    ? (layoutRaw as LiveLayoutId)
    : null;

  let monitorId: number | null = null;
  if (ds.monitor_id != null && ds.monitor_id !== '') {
    monitorId = parseMonitorId(String(ds.monitor_id));
  }

  let slot = 0;
  if (ds.slot != null && ds.slot !== '') {
    const n = Number(ds.slot);
    if (Number.isFinite(n) && n >= 0) slot = Math.floor(n);
  }

  const modeRaw = String(ds.mode || '').toLowerCase();
  let mode: AlarmDisplayMode = 'fullscreen';
  if (modeRaw === 'layout_switch' || (layout != null && modeRaw !== 'fullscreen')) {
    mode = layout ? 'layout_switch' : 'fullscreen';
  }

  const restoreOnReset = ds.restore_on_reset !== false;
  const cameraId = String(ds.camera_id || event.camera_id || '').trim() || undefined;

  return {
    mode,
    monitorId,
    layout,
    slot,
    restoreOnReset,
    cameraId,
  };
}

/** True when this Live View window should apply the switch (monitor match or unbound). */
export function displaySwitchTargetsMonitor(
  config: AlarmDisplaySwitchConfig | null,
  currentMonitorId: number,
): boolean {
  if (!config) return true;
  if (config.monitorId == null) return true;
  return parseMonitorId(String(config.monitorId)) === parseMonitorId(String(currentMonitorId));
}

export function shouldUseLayoutSwitch(config: AlarmDisplaySwitchConfig | null): boolean {
  return Boolean(config && config.mode === 'layout_switch' && config.layout);
}

/**
 * Apply alarm camera into configured layout/slot.
 * Returns next layout label + slots (does not mutate inputs).
 */
export function applyAlarmDisplaySwitch(opts: {
  config: AlarmDisplaySwitchConfig;
  cameraId: string;
  prevLayoutLabel: string;
  prevSlots: SlotAssignments;
  authorizedCameraIds: Set<string>;
}): { layoutLabel: string; slots: SlotAssignments } | null {
  const { config, cameraId, prevLayoutLabel, prevSlots, authorizedCameraIds } = opts;
  if (!shouldUseLayoutSwitch(config) || !config.layout) return null;
  if (!authorizedCameraIds.has(cameraId)) return null;

  const layout = layoutById(config.layout);
  const cols = layout.cols;
  let slots =
    prevLayoutLabel === layout.label || prevLayoutLabel === layout.id
      ? [...prevSlots]
      : migrateAssignmentsForLayout(
          prevSlots,
          layoutById(prevLayoutLabel).cols,
          cols,
          authorizedCameraIds,
        );
  const needed = slotCountForLayout(Math.max(slots.length, cols * cols), cols);
  if (slots.length < needed) {
    slots = [...slots, ...Array(needed - slots.length).fill(null)];
  } else if (slots.length > needed) {
    slots = slots.slice(0, needed);
  }
  const slotIndex = Math.min(config.slot, Math.max(0, slots.length - 1));
  slots = assignCameraToSlot(slots, slotIndex, cameraId);
  return { layoutLabel: layout.label, slots };
}

export function snapshotLiveViewState(
  eventId: string,
  layoutLabel: string,
  slots: SlotAssignments,
): LiveViewSnapshot {
  return {
    eventId,
    layoutLabel,
    slots: slots.map((s) => (s ? { ...s } : null)),
  };
}

export function alarmDisplaySwitchCapabilityPublic(): Record<string, unknown> {
  return {
    rdso_18_2_28: true,
    modes: ['fullscreen', 'layout_switch'],
    monitor_ids: Array.from({ length: MIN_LIVE_MONITORS }, (_, i) => i + 1),
    layouts: LIVE_LAYOUTS.map((l) => l.id),
    restore_on_reset: true,
    fallback_fullscreen_without_config: true,
    reuses_live_view_go2rtc: true,
    unauthorized_cameras_blocked: true,
  };
}

/** Build default empty slots for a layout (tests / seeding). */
export function emptySlotsForLayout(layoutId: string): SlotAssignments {
  const cols = layoutById(layoutId).cols;
  return buildDefaultAssignments([], cols);
}
