/** RDSO 18.2.5–18.2.7 — Live View display layouts (software capability).

Distinguishes software support from workstation/network acceptance for
16×@25fps simultaneous tiles.
*/

export type LiveLayoutId = '1x1' | '2x2' | '3x3' | '4x4' | '5x5' | '6x6';

export interface LiveLayoutDef {
  id: LiveLayoutId;
  cols: number;
  /** Short selector label */
  label: string;
  /** RDSO / operator-facing name */
  rdsoName: string;
  /** Simultaneous tile slots (= cols²) */
  tileCount: number;
  /** Clause tags for capability evidence */
  clauses: string[];
}

/** Minimum simultaneous live tiles required by RDSO 18.2.6 / 18.2.7.3 */
export const LIVE_DISPLAY_MIN_SIMULTANEOUS_TILES = 16;

/** Software does not throttle live decode below this — camera/stream source FPS applies. */
export const LIVE_DISPLAY_SOFTWARE_FPS_CAPABILITY = 25;

/**
 * Connect-attempt ramp only (not lifetime player count / not FPS).
 * Default must be ≥ 16 so a full 4×4 can start without an artificial soft-cap below RDSO.
 */
export const LIVE_CONNECT_RAMP_DEFAULT = 16;

export const LIVE_LAYOUTS: readonly LiveLayoutDef[] = [
  {
    id: '1x1',
    cols: 1,
    label: '1x1',
    rdsoName: 'Full screen',
    tileCount: 1,
    clauses: ['18.2.7.1'],
  },
  {
    id: '2x2',
    cols: 2,
    label: '2x2',
    rdsoName: 'Quad',
    tileCount: 4,
    clauses: ['18.2.7.2'],
  },
  {
    id: '3x3',
    cols: 3,
    label: '3x3',
    rdsoName: '3×3',
    tileCount: 9,
    clauses: ['18.2.7.4'],
  },
  {
    id: '4x4',
    cols: 4,
    label: '4x4',
    rdsoName: '4×4 (16 cameras)',
    tileCount: 16,
    clauses: ['18.2.6', '18.2.7.3'],
  },
  {
    id: '5x5',
    cols: 5,
    label: '5x5',
    rdsoName: '5×5',
    tileCount: 25,
    clauses: ['18.2.7.4'],
  },
  {
    id: '6x6',
    cols: 6,
    label: '6x6',
    rdsoName: '6×6',
    tileCount: 36,
    clauses: ['18.2.7.4'],
  },
] as const;

export function layoutById(id: string | null | undefined): LiveLayoutDef {
  const match = LIVE_LAYOUTS.find((l) => l.id === id || l.label === id);
  return match ?? LIVE_LAYOUTS[1];
}

export function layoutByCols(cols: number): LiveLayoutDef {
  const match = LIVE_LAYOUTS.find((l) => l.cols === cols);
  return match ?? LIVE_LAYOUTS[1];
}

/** Fixed slot count for a layout (always cols² — empty tiles allowed). */
export function layoutSlotCount(cols: number): number {
  const c = Math.max(1, Math.floor(cols));
  return c * c;
}

export function liveDisplayCapabilityPublic(): Record<string, unknown> {
  return {
    rdso_18_2_5: true,
    rdso_18_2_6: true,
    rdso_18_2_7: true,
    rdso_18_2_7_1: true,
    rdso_18_2_7_2: true,
    rdso_18_2_7_3: true,
    rdso_18_2_7_4: true,
    layouts: LIVE_LAYOUTS.map((l) => ({
      id: l.id,
      cols: l.cols,
      label: l.label,
      rdso_name: l.rdsoName,
      tile_count: l.tileCount,
      clauses: l.clauses,
    })),
    min_simultaneous_tiles: LIVE_DISPLAY_MIN_SIMULTANEOUS_TILES,
    max_layout_tiles: LIVE_LAYOUTS[LIVE_LAYOUTS.length - 1].tileCount,
    grid_uses_sub_stream: true,
    fullscreen_uses_main_stream: true,
    software_fps_capability: LIVE_DISPLAY_SOFTWARE_FPS_CAPABILITY,
    software_fps_throttle_below_25: false,
    connect_ramp_default: LIVE_CONNECT_RAMP_DEFAULT,
    connect_ramp_note:
      'VITE_GO2RTC_MAX_CONCURRENT limits simultaneous *connect attempts*, not lifetime players or FPS.',
    workstation_acceptance_required:
      'Sustained 16 cameras @ 25 fps each depends on workstation GPU/CPU, network, and camera encoder settings — not claimed by unit tests.',
    independent_monitors: 8,
  };
}
