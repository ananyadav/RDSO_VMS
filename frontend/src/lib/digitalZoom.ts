/** RDSO 18.2.21 / 18.2.22 — client-side digital zoom (no camera/PTZ movement).

Operates only on the displayed video element via CSS transform.
Does not call PTZ/ONVIF APIs, presets, or tours.
*/

export const DIGITAL_ZOOM_MIN = 1;
export const DIGITAL_ZOOM_MAX = 8;
export const DIGITAL_ZOOM_STEP = 0.25;

export type DigitalZoomState = {
  /** Magnification factor (1 = native view). */
  scale: number;
  /** Pan offset in CSS pixels (applied before scale, origin center). */
  panX: number;
  panY: number;
};

export const INITIAL_DIGITAL_ZOOM: DigitalZoomState = {
  scale: DIGITAL_ZOOM_MIN,
  panX: 0,
  panY: 0,
};

export function clampDigitalScale(scale: number): number {
  if (!Number.isFinite(scale)) return DIGITAL_ZOOM_MIN;
  return Math.min(DIGITAL_ZOOM_MAX, Math.max(DIGITAL_ZOOM_MIN, scale));
}

export function resetDigitalZoom(): DigitalZoomState {
  return { ...INITIAL_DIGITAL_ZOOM };
}

/** Max pan so the view stays within the scaled frame (viewport size in px). */
export function maxDigitalPan(scale: number, viewportWidth: number, viewportHeight: number): {
  x: number;
  y: number;
} {
  const s = clampDigitalScale(scale);
  if (s <= 1) return { x: 0, y: 0 };
  const w = Math.max(0, viewportWidth);
  const h = Math.max(0, viewportHeight);
  return {
    x: ((s - 1) / 2) * w,
    y: ((s - 1) / 2) * h,
  };
}

export function clampDigitalPan(
  state: DigitalZoomState,
  viewportWidth: number,
  viewportHeight: number,
): DigitalZoomState {
  const scale = clampDigitalScale(state.scale);
  if (scale <= 1) {
    return { scale: DIGITAL_ZOOM_MIN, panX: 0, panY: 0 };
  }
  const max = maxDigitalPan(scale, viewportWidth, viewportHeight);
  return {
    scale,
    panX: Math.min(max.x, Math.max(-max.x, state.panX)),
    panY: Math.min(max.y, Math.max(-max.y, state.panY)),
  };
}

export function setDigitalZoomScale(
  state: DigitalZoomState,
  nextScale: number,
  viewportWidth = 0,
  viewportHeight = 0,
): DigitalZoomState {
  const scale = clampDigitalScale(nextScale);
  if (scale <= 1) return resetDigitalZoom();
  return clampDigitalPan({ ...state, scale }, viewportWidth, viewportHeight);
}

export function zoomDigitalIn(
  state: DigitalZoomState,
  viewportWidth = 0,
  viewportHeight = 0,
  step = DIGITAL_ZOOM_STEP,
): DigitalZoomState {
  return setDigitalZoomScale(state, state.scale + step, viewportWidth, viewportHeight);
}

export function zoomDigitalOut(
  state: DigitalZoomState,
  viewportWidth = 0,
  viewportHeight = 0,
  step = DIGITAL_ZOOM_STEP,
): DigitalZoomState {
  return setDigitalZoomScale(state, state.scale - step, viewportWidth, viewportHeight);
}

export function panDigitalZoom(
  state: DigitalZoomState,
  deltaX: number,
  deltaY: number,
  viewportWidth: number,
  viewportHeight: number,
): DigitalZoomState {
  if (clampDigitalScale(state.scale) <= 1) return resetDigitalZoom();
  return clampDigitalPan(
    {
      ...state,
      panX: state.panX + deltaX,
      panY: state.panY + deltaY,
    },
    viewportWidth,
    viewportHeight,
  );
}

/** CSS transform for the existing live player container (aspect preserved via scale). */
export function digitalZoomCssTransform(state: DigitalZoomState): string {
  const scale = clampDigitalScale(state.scale);
  if (scale <= 1) return 'none';
  const x = Number.isFinite(state.panX) ? state.panX : 0;
  const y = Number.isFinite(state.panY) ? state.panY : 0;
  return `translate(${x}px, ${y}px) scale(${scale})`;
}

export function digitalZoomLabel(scale: number): string {
  const s = clampDigitalScale(scale);
  const rounded = Math.round(s * 100) / 100;
  return `${rounded}×`;
}

/** Capability evidence for RDSO acceptance (software). */
export function digitalZoomCapabilityPublic(): Record<string, unknown> {
  return {
    rdso_18_2_21: true,
    rdso_18_2_22: true,
    client_side_only: true,
    affects_camera_ptz: false,
    affects_presets_tours: false,
    min_scale: DIGITAL_ZOOM_MIN,
    max_scale: DIGITAL_ZOOM_MAX,
    step: DIGITAL_ZOOM_STEP,
    pan_while_zoomed: true,
    reset_on_camera_change: true,
    fixed_cameras: true,
    ptz_cameras: true,
    optical_ptz_separate: true,
    fullscreen: true,
  };
}
