import { describe, expect, it } from 'vitest';
import { buildGo2RtcStreamSrc, go2rtcWsPath } from './mediaUrls';

describe('RDSO 18.2.2 relative media URLs', () => {
  it('builds relative /media/wN paths (LAN/WAN/WLAN safe)', () => {
    expect(go2rtcWsPath(1)).toBe('/media/w1/api/ws');
    expect(go2rtcWsPath(3)).toBe('/media/w3/api/ws');
    const src = buildGo2RtcStreamSrc('cam_sub', 2);
    expect(src.startsWith('/media/w2/api/ws')).toBe(true);
    expect(src.includes('://')).toBe(false);
    expect(src.includes('127.0.0.1')).toBe(false);
  });
});
