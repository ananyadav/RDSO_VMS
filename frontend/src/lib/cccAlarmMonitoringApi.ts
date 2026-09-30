/** RDSO 18.6.22.9 — CCC alarm monitoring API. */

import { apiFetch, readJsonResponse } from './api';

export type CccAffectedZone = {
  zone?: string | null;
  zone_key?: string | null;
  source?: string;
  location_path?: string | null;
  unknown?: boolean;
  geo_distance_used?: boolean;
  note?: string;
};

export type CccAlarmSnapshot = {
  on_demand?: boolean;
  via_vms_go2rtc?: boolean;
  frame_jpeg_path?: string | null;
  src?: string | null;
  worker_id?: number;
  direct_camera?: boolean;
};

export type CccAlarmItem = {
  event_id: string;
  title: string;
  message?: string;
  source_type?: string;
  type?: string;
  severity: string;
  priority: number;
  status: string;
  state?: string;
  occurred_at?: string | null;
  elapsed_seconds?: number | null;
  affected_zone?: CccAffectedZone;
  camera_id?: string | null;
  camera_authorized?: boolean;
  ptz?: boolean;
  selection_reason?: string;
  snapshot?: CccAlarmSnapshot | null;
  media?: {
    worker_id?: number;
    ws_path?: string;
    stream_src_hint?: string;
  } | null;
  live_href?: string | null;
  via_vms_only?: boolean;
  direct_camera_rtsp?: boolean;
  camera_response?: {
    camera_name?: string | null;
    selection_reason?: string;
    ptz?: boolean;
  };
};

export type CccAlarmMonitoring = {
  monitoring_state: 'MONITORING' | 'ALARM' | string;
  monitoring_label?: string;
  is_alarm: boolean;
  active_count: number;
  items: CccAlarmItem[];
  total: number;
  poll_seconds_suggested?: number;
  video_path?: string;
  direct_camera_rtsp_forbidden?: boolean;
  rdso_18_6_22_9?: boolean;
};

export type CccZoneCameraMapping = {
  zone: string;
  zone_key: string;
  preferred_camera_id: string;
  prefer_ptz?: boolean;
};

export async function fetchCccAlarmMonitoring(limit = 20): Promise<CccAlarmMonitoring> {
  const res = await apiFetch(`/api/ccc/alarm-monitoring?limit=${limit}`);
  return readJsonResponse(res);
}

export async function recoverCccAlarm(
  eventId: string,
  reason = 'operator_reset',
): Promise<{ ok: boolean; event: Record<string, unknown>; monitoring: CccAlarmMonitoring }> {
  const res = await apiFetch(`/api/ccc/alarm-monitoring/${encodeURIComponent(eventId)}/recover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
  return readJsonResponse(res);
}

export async function fetchCccZoneCameraMap(): Promise<{ mappings: CccZoneCameraMapping[] }> {
  const res = await apiFetch('/api/ccc/zone-camera-map');
  return readJsonResponse(res);
}

export async function saveCccZoneCameraMap(
  mappings: CccZoneCameraMapping[],
): Promise<{ ok: boolean; mappings: CccZoneCameraMapping[] }> {
  const res = await apiFetch('/api/ccc/admin/zone-camera-map', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mappings }),
  });
  return readJsonResponse(res);
}

export async function fetchCccCameraSnapshot(
  cameraId: string,
): Promise<{ snapshot: CccAlarmSnapshot; direct_camera_rtsp: boolean }> {
  const res = await apiFetch(`/api/ccc/cameras/${encodeURIComponent(cameraId)}/snapshot`);
  return readJsonResponse(res);
}
