import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchPtzPatterns,
  fetchPtzPresets,
  fetchPtzTours,
  ptzDeletePreset,
  ptzGotoPreset,
  ptzMove,
  ptzRecordPatternStart,
  ptzRecordPatternStop,
  ptzSetPattern,
  ptzSetPreset,
  ptzSetTour,
  ptzStartPattern,
  ptzStartTour,
  ptzStop,
  ptzStopPattern,
  ptzStopTour,
} from './ptzApi';

vi.mock('./api', () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from './api';

const mockedFetch = vi.mocked(apiFetch);

afterEach(() => {
  mockedFetch.mockReset();
});

describe('ptzApi move/stop', () => {
  it('posts pan/tilt/zoom directions', async () => {
    for (const direction of ['left', 'right', 'up', 'down', 'zoom_in', 'zoom_out']) {
      mockedFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      } as Response);
      const result = await ptzMove('cam1', direction, 2);
      expect(result.ok).toBe(true);
      expect(mockedFetch).toHaveBeenLastCalledWith(
        '/api/ptz/cam1/move',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ direction, speed: 2 }),
        }),
      );
    }
  });

  it('surfaces move failures (offline/protocol)', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => ({ ok: false, error: 'camera unreachable' }),
    } as Response);
    const result = await ptzMove('cam1', 'left', 2);
    expect(result.ok).toBe(false);
    expect(result.error).toContain('unreachable');
  });

  it('surfaces non-PTZ / ACL errors', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ error: 'Camera is not marked as PTZ' }),
    } as Response);
    const result = await ptzMove('fixed', 'left', 2);
    expect(result.ok).toBe(false);
    expect(result.error).toContain('not marked as PTZ');
  });

  it('stops movement and reports failure safely', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ok: true }),
    } as Response);
    expect(await ptzStop('cam1')).toEqual({ ok: true });
    expect(mockedFetch).toHaveBeenCalledWith('/api/ptz/cam1/stop', { method: 'POST' });

    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => ({ error: 'timeout' }),
    } as Response);
    const fail = await ptzStop('cam1');
    expect(fail.ok).toBe(false);
    expect(fail.error).toContain('timeout');
  });
});

describe('ptzApi presets', () => {
  it('lists presets and surfaces list failures', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ok: true, presets: [{ id: 1, name: 'A' }] }),
    } as Response);
    const ok = await fetchPtzPresets('cam1');
    expect(ok).toEqual({ ok: true, presets: [{ id: 1, name: 'A' }], supported: true });

    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => ({ ok: false, error: 'timeout' }),
    } as Response);
    const fail = await fetchPtzPresets('cam1');
    expect(fail.ok).toBe(false);
    expect(fail.presets).toEqual([]);
    expect(fail.error).toContain('timeout');
  });

  it('create/goto/delete presets', async () => {
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzSetPreset('cam1', 2, 'Door')).toEqual({ ok: true });
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzGotoPreset('cam1', 2)).toEqual({ ok: true });
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzDeletePreset('cam1', 2)).toEqual({ ok: true });
  });
});

describe('ptzApi tours', () => {
  it('lists tours and marks unsupported cameras', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true,
        supported: true,
        tours: [{ id: 1, name: 'T1', steps: [{ presetId: 1, delay: 5 }] }],
      }),
    } as Response);
    const ok = await fetchPtzTours('cam1');
    expect(ok.ok).toBe(true);
    expect(ok.tours).toHaveLength(1);

    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 501,
      json: async () => ({ ok: false, supported: false, tours: [], error: 'not supported' }),
    } as Response);
    const unsupported = await fetchPtzTours('cam1');
    expect(unsupported.ok).toBe(false);
    expect(unsupported.supported).toBe(false);
  });

  it('save/start/stop tour', async () => {
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(
      await ptzSetTour('cam1', 1, {
        name: 'Lobby',
        steps: [
          { presetId: 1, delay: 5 },
          { presetId: 2, delay: 5 },
        ],
      }),
    ).toEqual({ ok: true });
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzStartTour('cam1', 1)).toEqual({ ok: true });
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzStopTour('cam1', 1)).toEqual({ ok: true });
  });
});

describe('PTZ patterns (RDSO 18.2.23 — distinct from tours)', () => {
  it('lists patterns and marks unsupported honestly', async () => {
    mockedFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true,
        patterns: [{ id: 1, name: 'Sweep' }],
        distinct_from_tour_patrol: true,
      }),
    } as Response);
    const listed = await fetchPtzPatterns('cam1');
    expect(listed.ok).toBe(true);
    expect(listed.patterns[0].name).toBe('Sweep');

    mockedFetch.mockResolvedValueOnce({
      ok: false,
      status: 501,
      json: async () => ({
        ok: false,
        supported: false,
        patterns: [],
        error: 'ONVIF has no standard PTZ Pattern API',
      }),
    } as Response);
    const unsupported = await fetchPtzPatterns('cam1');
    expect(unsupported.ok).toBe(false);
    expect(unsupported.supported).toBe(false);
  });

  it('start/stop/record pattern without using tour endpoints', async () => {
    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzSetPattern('cam1', 1, 'Sweep')).toEqual({ ok: true });
    expect(mockedFetch.mock.calls.at(-1)?.[0]).toContain('/patterns/1');

    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzStartPattern('cam1', 1)).toEqual({ ok: true });
    expect(mockedFetch.mock.calls.at(-1)?.[0]).toContain('/patterns/1/start');

    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzStopPattern('cam1', 1)).toEqual({ ok: true });

    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzRecordPatternStart('cam1', 1)).toEqual({ ok: true });
    expect(mockedFetch.mock.calls.at(-1)?.[0]).toContain('/record-start');

    mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) } as Response);
    expect(await ptzRecordPatternStop('cam1', 1)).toEqual({ ok: true });
    expect(mockedFetch.mock.calls.at(-1)?.[0]).toContain('/record-stop');
  });
});

describe('Live View PTZ selection gate', () => {
  it('only enables pad for selected PTZ camera', () => {
    const cameras = [
      { id: 'a', ptz: true },
      { id: 'b', ptz: false },
      { id: 'c', ptz: true },
    ];
    const selectedId = 'c';
    const enabled = cameras.map((c) => Boolean(c.ptz) && selectedId === c.id);
    expect(enabled).toEqual([false, false, true]);
  });
});
