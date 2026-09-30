/**
 * RDSO 18.6 — CCC → VMS integration abstraction (browser).
 *
 * CCC never talks to camera RTSP/ONVIF. All operations go through VMS REST +
 * relative media paths. LocalVmsSource is the first (and currently only) source.
 */

import { apiFetch } from './api';
import { go2rtcWsPath } from './mediaUrls';

export type CccCamera = {
  id: string;
  camera_uid?: string;
  cameraUid?: string;
  name: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
  ptz?: boolean;
  camera_group?: string;
  location_path?: string;
  workerId?: number | string | null;
  recording_home_server_id?: string | null;
  identity_stable?: boolean;
  password?: null;
  main_rtsp_url?: null;
  sub_rtsp_url?: null;
  rtsp_url?: null;
};

export type CccSourceDescribe = {
  source_id: string;
  label: string;
  kind: string;
  browser_only: boolean;
  requires_separate_client: boolean;
  direct_camera_rtsp: boolean;
  video_path: string;
  external_vendor_integration?: boolean;
};

export type CccClientMedia = {
  camera_id?: string;
  camera_uid?: string;
  live?: {
    worker_id?: number;
    ws_path?: string;
    stream_src_hint?: string;
    relative_path?: boolean;
  };
  playback?: {
    media_path_template?: string;
  };
  ccc_no_direct_camera?: boolean;
  ccc_browser_only?: boolean;
  [key: string]: unknown;
};

export interface CccVmsSource {
  readonly sourceId: string;
  describe(): Promise<CccSourceDescribe>;
  listCameras(opts?: {
    limit?: number;
    offset?: number;
    q?: string;
  }): Promise<{ items: CccCamera[]; total: number; limit: number; offset: number }>;
  clientMedia(cameraId: string): Promise<CccClientMedia>;
  status(): Promise<Record<string, unknown>>;
  capability(): Promise<Record<string, unknown>>;
}

/** Assert a media path is VMS-relative — never raw camera RTSP. */
export function assertVmsMediaPath(path: string): string {
  const p = (path || '').trim();
  if (!p) throw new Error('Empty media path');
  if (/^rtsp:\/\//i.test(p)) {
    throw new Error('CCC must not use direct camera RTSP');
  }
  if (!p.startsWith('/')) {
    throw new Error('CCC media paths must be relative VMS URLs');
  }
  return p;
}

export function liveWsPathFromMedia(media: CccClientMedia): string {
  const wid = Number(media.live?.worker_id || 1);
  const fromApi = media.live?.ws_path;
  if (fromApi) return assertVmsMediaPath(fromApi);
  return assertVmsMediaPath(go2rtcWsPath(wid > 0 ? wid : 1));
}

export function playbackPathTemplate(media: CccClientMedia): string {
  const t =
    media.playback?.media_path_template ||
    '/api/playback/{camera_id}/{session_id}/media/index.m3u8';
  return assertVmsMediaPath(t.replace(/\{[^}]+\}/g, 'x'));
}

export function cameraHasNoCredentials(cam: CccCamera): boolean {
  if (cam.password) return false;
  if (cam.main_rtsp_url || cam.sub_rtsp_url || cam.rtsp_url) return false;
  return true;
}

export function payloadHasNoRtspSecrets(obj: unknown): boolean {
  const raw = JSON.stringify(obj ?? {});
  if (/rtsp:\/\//i.test(raw)) return false;
  if (/"password"\s*:\s*"[^"*]+"/i.test(raw) && !/"password"\s*:\s*null/i.test(raw)) {
    // allow null password fields
    if (/"password"\s*:\s*"(?!null)[^"]+"/i.test(raw)) return false;
  }
  return true;
}

/** Paginate a large logical list without mounting all streams. */
export function pageCameras<T>(
  items: T[],
  offset: number,
  limit: number,
): { page: T[]; total: number; offset: number; limit: number } {
  const total = items.length;
  const off = Math.max(0, offset | 0);
  const lim = Math.max(1, limit | 0);
  return { page: items.slice(off, off + lim), total, offset: off, limit: lim };
}

export class LocalCccVmsSource implements CccVmsSource {
  readonly sourceId = 'local';

  async describe(): Promise<CccSourceDescribe> {
    const res = await apiFetch('/api/ccc/sources');
    if (!res.ok) throw new Error('Failed to load CCC sources');
    const data = await res.json();
    const item = (data.items || []).find((s: CccSourceDescribe) => s.source_id === 'local');
    return (
      item || {
        source_id: 'local',
        label: 'Local VMS',
        kind: 'local_vms',
        browser_only: true,
        requires_separate_client: false,
        direct_camera_rtsp: false,
        video_path: 'CCC UI → VMS API/media → go2rtc/recordings',
      }
    );
  }

  async listCameras(opts?: {
    limit?: number;
    offset?: number;
    q?: string;
  }): Promise<{ items: CccCamera[]; total: number; limit: number; offset: number }> {
    const q = new URLSearchParams();
    q.set('source', this.sourceId);
    q.set('limit', String(opts?.limit ?? 100));
    q.set('offset', String(opts?.offset ?? 0));
    if (opts?.q) q.set('q', opts.q);
    const res = await apiFetch(`/api/ccc/cameras?${q.toString()}`);
    if (!res.ok) throw new Error('Failed to list CCC cameras');
    const data = await res.json();
    return {
      items: data.items || [],
      total: data.total ?? 0,
      limit: data.limit ?? opts?.limit ?? 100,
      offset: data.offset ?? 0,
    };
  }

  async clientMedia(cameraId: string): Promise<CccClientMedia> {
    const res = await apiFetch(
      `/api/ccc/cameras/${encodeURIComponent(cameraId)}/client-media?source=${this.sourceId}`,
    );
    if (!res.ok) throw new Error('Failed to load client-media');
    return res.json();
  }

  async status(): Promise<Record<string, unknown>> {
    const res = await apiFetch('/api/ccc/status');
    if (!res.ok) throw new Error('Failed to load CCC status');
    return res.json();
  }

  async capability(): Promise<Record<string, unknown>> {
    const res = await apiFetch('/api/ccc/capability');
    if (!res.ok) throw new Error('Failed to load CCC capability');
    return res.json();
  }
}

/** Routed CCC source — passes ?source= to VMS adapter layer. */
export class RoutedCccVmsSource implements CccVmsSource {
  constructor(readonly sourceId: string) {}

  async describe(): Promise<CccSourceDescribe> {
    const res = await apiFetch('/api/ccc/sources');
    if (!res.ok) throw new Error('Failed to load CCC sources');
    const data = await res.json();
    const item = (data.items || []).find(
      (s: CccSourceDescribe) => s.source_id === this.sourceId,
    );
    if (!item) throw new Error(`Unknown CCC source: ${this.sourceId}`);
    return item;
  }

  async listCameras(opts?: {
    limit?: number;
    offset?: number;
    q?: string;
  }): Promise<{ items: CccCamera[]; total: number; limit: number; offset: number }> {
    const q = new URLSearchParams();
    q.set('source', this.sourceId);
    q.set('limit', String(opts?.limit ?? 100));
    q.set('offset', String(opts?.offset ?? 0));
    if (opts?.q) q.set('q', opts.q);
    const res = await apiFetch(`/api/ccc/cameras?${q.toString()}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as { error?: string }).error || 'Failed to list CCC cameras');
    }
    const data = await res.json();
    return {
      items: data.items || [],
      total: data.total ?? 0,
      limit: data.limit ?? opts?.limit ?? 100,
      offset: data.offset ?? 0,
    };
  }

  async clientMedia(cameraId: string): Promise<CccClientMedia> {
    const res = await apiFetch(
      `/api/ccc/cameras/${encodeURIComponent(cameraId)}/client-media?source=${encodeURIComponent(this.sourceId)}`,
    );
    if (!res.ok) throw new Error('Failed to load client-media');
    return res.json();
  }

  async status(): Promise<Record<string, unknown>> {
    const res = await apiFetch('/api/ccc/status');
    if (!res.ok) throw new Error('Failed to load CCC status');
    return res.json();
  }

  async capability(): Promise<Record<string, unknown>> {
    const res = await apiFetch('/api/ccc/capability');
    if (!res.ok) throw new Error('Failed to load CCC capability');
    return res.json();
  }
}

const _sourceCache = new Map<string, CccVmsSource>();

export function getCccVmsSource(sourceId = 'local'): CccVmsSource {
  const sid = (sourceId || 'local').trim() || 'local';
  if (sid === 'local') {
    const cached = _sourceCache.get('local');
    if (cached) return cached;
    const local = new LocalCccVmsSource();
    _sourceCache.set('local', local);
    return local;
  }
  let src = _sourceCache.get(sid);
  if (!src) {
    src = new RoutedCccVmsSource(sid);
    _sourceCache.set(sid, src);
  }
  return src;
}

export async function listCccSourceOptions(): Promise<CccSourceDescribe[]> {
  const res = await apiFetch('/api/ccc/sources');
  if (!res.ok) return [];
  const data = await res.json();
  return data.items || [];
}

/** Test hook — inject a source implementation without faking vendor SDKs in production. */
export function setCccVmsSourceForTests(source: CccVmsSource | null): void {
  _sourceCache.clear();
  if (source) _sourceCache.set(source.sourceId, source);
}

export function cccPlaybackHref(cameraId: string): string {
  return `/playback?camera=${encodeURIComponent(cameraId)}`;
}

export function cccLiveHref(cameraIds: string[]): string {
  if (cameraIds.length === 1) {
    return `/live?camera=${encodeURIComponent(cameraIds[0])}`;
  }
  return '/live';
}

export function cccCapabilityFlagsPublic(): Record<string, unknown> {
  return {
    rdso_18_6: true,
    browser_only: true,
    requires_separate_client: false,
    direct_camera_rtsp_forbidden: true,
    uses_vms_layer: true,
    ui_path: '/ccc',
  };
}
