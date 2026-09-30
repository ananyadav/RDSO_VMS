import { describe, expect, it } from 'vitest';
import {
  assertVmsMediaPath,
  cameraHasNoCredentials,
  cccCapabilityFlagsPublic,
  cccPlaybackHref,
  liveWsPathFromMedia,
  pageCameras,
  payloadHasNoRtspSecrets,
} from './cccVmsSource';

describe('RDSO 18.6 CCC → VMS abstraction', () => {
  it('rejects direct camera RTSP for CCC media', () => {
    expect(() => assertVmsMediaPath('rtsp://10.0.0.5/stream')).toThrow(/direct camera RTSP/i);
    expect(assertVmsMediaPath('/media/w1/api/ws')).toBe('/media/w1/api/ws');
  });

  it('live route uses VMS relative go2rtc path', () => {
    const path = liveWsPathFromMedia({
      live: { worker_id: 2, ws_path: '/media/w2/api/ws' },
      ccc_no_direct_camera: true,
    });
    expect(path).toBe('/media/w2/api/ws');
    expect(path).not.toMatch(/^rtsp:/i);
  });

  it('playback opens existing VMS playback route', () => {
    expect(cccPlaybackHref('cam1')).toBe('/playback?camera=cam1');
  });

  it('pages large logical camera datasets without streaming all', () => {
    const all = Array.from({ length: 2500 }, (_, i) => ({ id: `c${i}` }));
    const page = pageCameras(all, 100, 50);
    expect(page.total).toBe(2500);
    expect(page.page).toHaveLength(50);
    expect(page.page[0].id).toBe('c100');
  });

  it('camera descriptors must not expose credentials', () => {
    expect(
      cameraHasNoCredentials({
        id: '1',
        name: 'Gate',
        online: true,
        password: null,
        main_rtsp_url: null,
      }),
    ).toBe(true);
    expect(
      cameraHasNoCredentials({
        id: '1',
        name: 'Gate',
        online: true,
        password: 'secret',
      }),
    ).toBe(false);
  });

  it('client-media payload must not contain rtsp secrets', () => {
    expect(
      payloadHasNoRtspSecrets({
        live: { ws_path: '/media/w1/api/ws' },
        password: null,
      }),
    ).toBe(true);
    expect(
      payloadHasNoRtspSecrets({
        url: 'rtsp://admin:pass@10.0.0.1/h264',
      }),
    ).toBe(false);
  });

  it('browser-only capability flags', () => {
    const cap = cccCapabilityFlagsPublic();
    expect(cap.browser_only).toBe(true);
    expect(cap.requires_separate_client).toBe(false);
    expect(cap.direct_camera_rtsp_forbidden).toBe(true);
    expect(cap.ui_path).toBe('/ccc');
  });
});
