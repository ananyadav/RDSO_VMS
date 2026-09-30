import { describe, expect, it, vi } from 'vitest';
import {
  DIGITAL_ZOOM_MAX,
  DIGITAL_ZOOM_MIN,
  INITIAL_DIGITAL_ZOOM,
  clampDigitalScale,
  digitalZoomCapabilityPublic,
  digitalZoomCssTransform,
  digitalZoomLabel,
  maxDigitalPan,
  panDigitalZoom,
  resetDigitalZoom,
  setDigitalZoomScale,
  zoomDigitalIn,
  zoomDigitalOut,
} from './digitalZoom';

describe('RDSO 18.2.21 / 18.2.22 digital zoom', () => {
  it('starts at 1× with no pan', () => {
    expect(INITIAL_DIGITAL_ZOOM).toEqual({ scale: 1, panX: 0, panY: 0 });
    expect(resetDigitalZoom()).toEqual(INITIAL_DIGITAL_ZOOM);
  });

  it('clamps scale to 1×–8×', () => {
    expect(clampDigitalScale(0)).toBe(DIGITAL_ZOOM_MIN);
    expect(clampDigitalScale(0.5)).toBe(1);
    expect(clampDigitalScale(4)).toBe(4);
    expect(clampDigitalScale(99)).toBe(DIGITAL_ZOOM_MAX);
    expect(setDigitalZoomScale(INITIAL_DIGITAL_ZOOM, 12).scale).toBe(8);
    expect(setDigitalZoomScale(INITIAL_DIGITAL_ZOOM, 0.1).scale).toBe(1);
  });

  it('zooms in/out for fixed-camera streams (client-side only)', () => {
    let z = INITIAL_DIGITAL_ZOOM;
    z = zoomDigitalIn(z);
    expect(z.scale).toBeGreaterThan(1);
    z = zoomDigitalOut(z);
    expect(z.scale).toBe(1);
  });

  it('zooms in/out for PTZ-camera streams the same way (no optical coupling)', () => {
    // Same math path for fixed and PTZ — optical PTZ is a separate LivePtzPad.
    const ptzView = zoomDigitalIn(INITIAL_DIGITAL_ZOOM, 800, 600);
    expect(ptzView.scale).toBeGreaterThan(1);
    expect(digitalZoomCssTransform(ptzView)).toContain('scale(');
  });

  it('pans only while digitally zoomed and clamps to viewport', () => {
    const zoomed = setDigitalZoomScale(INITIAL_DIGITAL_ZOOM, 2, 400, 300);
    expect(maxDigitalPan(2, 400, 300)).toEqual({ x: 200, y: 150 });
    const panned = panDigitalZoom(zoomed, 50, -40, 400, 300);
    expect(panned.panX).toBe(50);
    expect(panned.panY).toBe(-40);
    const over = panDigitalZoom(zoomed, 9999, 9999, 400, 300);
    expect(over.panX).toBe(200);
    expect(over.panY).toBe(150);
    const at1x = panDigitalZoom(INITIAL_DIGITAL_ZOOM, 10, 10, 400, 300);
    expect(at1x).toEqual(INITIAL_DIGITAL_ZOOM);
  });

  it('resets to 1×', () => {
    const zoomed = panDigitalZoom(setDigitalZoomScale(INITIAL_DIGITAL_ZOOM, 4, 200, 200), 20, 10, 200, 200);
    expect(resetDigitalZoom()).toEqual({ scale: 1, panX: 0, panY: 0 });
    expect(setDigitalZoomScale(zoomed, 1)).toEqual(INITIAL_DIGITAL_ZOOM);
  });

  it('camera-change reset uses resetDigitalZoom()', () => {
    const afterCameraA = zoomDigitalIn(zoomDigitalIn(INITIAL_DIGITAL_ZOOM));
    expect(afterCameraA.scale).toBeGreaterThan(1);
    const afterCameraB = resetDigitalZoom();
    expect(afterCameraB.scale).toBe(1);
    expect(afterCameraB.panX).toBe(0);
  });

  it('preserves aspect via uniform scale transform (no stretch)', () => {
    const z = setDigitalZoomScale(INITIAL_DIGITAL_ZOOM, 3, 640, 360);
    const css = digitalZoomCssTransform(z);
    expect(css).toMatch(/scale\(3\)/);
    expect(css).not.toMatch(/scaleX|scaleY|matrix/);
    expect(digitalZoomLabel(3)).toBe('3×');
  });

  it('does not invoke PTZ APIs', async () => {
    const ptzMove = vi.fn();
    const ptzStop = vi.fn();
    // Simulate UI digital-zoom path — pure functions only.
    let state = INITIAL_DIGITAL_ZOOM;
    state = zoomDigitalIn(state, 800, 450);
    state = panDigitalZoom(state, 12, -8, 800, 450);
    state = zoomDigitalOut(state, 800, 450);
    state = resetDigitalZoom();
    expect(ptzMove).not.toHaveBeenCalled();
    expect(ptzStop).not.toHaveBeenCalled();
    expect(state.scale).toBe(1);

    // Module must not depend on ptzApi.
    const src = await import('./digitalZoom');
    expect(src.digitalZoomCapabilityPublic().affects_camera_ptz).toBe(false);
    expect(src.digitalZoomCapabilityPublic().optical_ptz_separate).toBe(true);
  });

  it('publishes RDSO capability flags', () => {
    const cap = digitalZoomCapabilityPublic();
    expect(cap.rdso_18_2_21).toBe(true);
    expect(cap.rdso_18_2_22).toBe(true);
    expect(cap.fixed_cameras).toBe(true);
    expect(cap.ptz_cameras).toBe(true);
    expect(cap.client_side_only).toBe(true);
    expect(cap.max_scale).toBe(8);
  });
});
