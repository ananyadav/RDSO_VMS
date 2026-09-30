/** RDSO 18.1.27 / 18.2.24–18.2.26 — logical Live View display / monitor identities.

Physical multi-monitor / 55" LFD placement is OS + workstation acceptance —
this module does not hardcode a physical display resolution.
*/

import type { SlotAssignments } from './liveTileAssignments';

/** Minimum independent Live View windows / monitor identities per workstation. */
export const MIN_LIVE_MONITORS = 8;

export const LIVE_MONITOR_IDS = Array.from({ length: MIN_LIVE_MONITORS }, (_, i) => i + 1);

export interface LiveMonitorPersistedState {
  layoutLabel: string;
  site: string | null;
  building: string | null;
  group: string | null;
  slots: SlotAssignments;
  updatedAt: string;
}

export function parseMonitorId(raw: string | null | undefined): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(MIN_LIVE_MONITORS, Math.max(1, Math.floor(n)));
}

export function liveMonitorStorageKey(monitorId: number): string {
  const id = parseMonitorId(String(monitorId));
  return `cctv.liveMonitor.v1.${id}`;
}

function storage(): Storage | null {
  if (typeof globalThis === 'undefined') return null;
  try {
    const ls = (globalThis as { localStorage?: Storage }).localStorage;
    if (!ls) return null;
    return ls;
  } catch {
    return null;
  }
}

export function loadLiveMonitorState(monitorId: number): LiveMonitorPersistedState | null {
  const ls = storage();
  if (!ls) return null;
  try {
    const raw = ls.getItem(liveMonitorStorageKey(monitorId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LiveMonitorPersistedState;
    if (!parsed || typeof parsed !== 'object') return null;
    return {
      layoutLabel: String(parsed.layoutLabel || ''),
      site: parsed.site ?? null,
      building: parsed.building ?? null,
      group: parsed.group ?? null,
      slots: Array.isArray(parsed.slots) ? parsed.slots : [],
      updatedAt: String(parsed.updatedAt || ''),
    };
  } catch {
    return null;
  }
}

export function saveLiveMonitorState(
  monitorId: number,
  state: Omit<LiveMonitorPersistedState, 'updatedAt'> & { updatedAt?: string },
): void {
  const ls = storage();
  if (!ls) return;
  const payload: LiveMonitorPersistedState = {
    layoutLabel: state.layoutLabel,
    site: state.site,
    building: state.building,
    group: state.group,
    slots: state.slots,
    updatedAt: state.updatedAt || new Date().toISOString(),
  };
  try {
    ls.setItem(liveMonitorStorageKey(monitorId), JSON.stringify(payload));
  } catch {
    // Quota / private mode — ignore
  }
}

/** Path for an independent Live View display window (client-PC control). */
export function liveMonitorPath(monitorId: number): string {
  return `/live?monitor=${parseMonitorId(String(monitorId))}`;
}

/**
 * Open an independent Live View window for monitor N (logical display identity).
 * Operator can place the window on a workstation or external/LFD via the OS.
 * Do not put `noopener` in window features — browsers then return null even on success.
 */
export function openLiveMonitorWindow(monitorId: number): Window | null {
  const id = parseMonitorId(String(monitorId));
  if (typeof window === 'undefined') return null;
  const target = `cctv-live-monitor-${id}`;
  const path = liveMonitorPath(id);
  try {
    const win = window.open(path, target);
    if (win) {
      try {
        // Soften opener link without nulling the open() return value.
        (win as Window & { opener: Window | null }).opener = null;
      } catch {
        /* ignore */
      }
    }
    return win;
  } catch {
    return null;
  }
}

export function listIndependentMonitorStates(): Array<{
  monitorId: number;
  state: LiveMonitorPersistedState | null;
}> {
  return LIVE_MONITOR_IDS.map((monitorId) => ({
    monitorId,
    state: loadLiveMonitorState(monitorId),
  }));
}

/**
 * Tile row height from the current browser viewport (RDSO 18.2.24).
 * No fixed 55"/4K/workstation pixel size — scales to whatever display hosts the window.
 */
export function computeLiveGridRowHeight(
  viewportWidth: number,
  viewportHeight: number,
  gridCols: number,
  gapPx = 2,
): number {
  const cols = Math.max(1, Math.floor(gridCols) || 1);
  const h = Math.max(0, viewportHeight);
  const w = Math.max(0, viewportWidth);
  if (h <= 0) return 0;
  const minRow = cols >= 6 ? 48 : cols >= 5 ? 64 : 80;
  const isPhone = w > 0 && w < 768;
  if (isPhone) {
    if (cols === 1) return Math.max(minRow, h);
    return Math.max(minRow, Math.ceil((h - gapPx * (cols - 1)) / cols));
  }
  return Math.max(minRow, Math.ceil((h - gapPx * (cols - 1)) / cols));
}

/** Capability evidence for RDSO 18.2.24–18.2.26 (software; not physical LFD soak). */
export function liveDisplayMonitorControlCapabilityPublic(): Record<string, unknown> {
  return {
    rdso_18_2_24: true,
    rdso_18_2_25: true,
    rdso_18_2_26: true,
    resolution_responsive: true,
    hardcoded_physical_display_resolution: false,
    physical_55_inch_tested: false,
    client_pc_display_control: true,
    open_display_from_client: true,
    select_monitor_identity: true,
    select_layout_from_client: true,
    assign_cameras_per_slot: true,
    independent_monitor_layout_and_cameras: true,
    logical_monitors: MIN_LIVE_MONITORS,
    layouts: ['1x1', '2x2', '3x3', '4x4', '5x5', '6x6'],
    fullscreen_display_window: true,
    server_console_required: false,
    physical_lfd_workstation_acceptance_required:
      'Place Live View / fullscreen windows on target workstation and 55" LFD via OS multi-monitor; not claimed by unit tests.',
  };
}
