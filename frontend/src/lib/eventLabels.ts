/** Display labels for alarm events (not AI analytics events). */

import { triggerLabel } from './alarmRuleLabels';

export type AlarmEventSeverity = 'info' | 'warning' | 'critical';
export type AlarmEventStatus = 'open' | 'acknowledged';

export const EVENT_SOURCE_OPTIONS = [
  { value: '', label: 'All sources' },
  { value: 'signal_loss', label: 'Signal Loss', available: true },
  { value: 'motion', label: 'Motion (not yet available)', available: false },
  { value: 'digital_input', label: 'Digital Input (not yet available)', available: false },
  { value: 'recording_failure', label: 'Recording Failure (not yet available)', available: false },
  { value: 'manual_test', label: 'Manual Test (not yet available)', available: false },
] as const;

export const EVENT_SEVERITY_OPTIONS = [
  { value: '', label: 'All severities' },
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
] as const;

export const EVENT_STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'open', label: 'Open' },
  { value: 'acknowledged', label: 'Acknowledged' },
] as const;

export const ACK_FILTER_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'false', label: 'Unacknowledged' },
  { value: 'true', label: 'Acknowledged' },
] as const;

export function sourceTypeLabel(sourceType: string): string {
  if (!sourceType) return '—';
  return triggerLabel(sourceType);
}

export function recordingStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'started':
      return 'Recording started';
    case 'already_recording':
      return 'Already recording';
    case 'extended':
      return 'Recording extended';
    case 'engine_disabled':
      return 'Recording engine disabled';
    case 'master_disabled':
      return 'Recording master disabled';
    case 'failed':
      return 'Recording failed';
    default:
      return status || '—';
  }
}

export function severityBadgeClass(severity: string): string {
  switch (severity) {
    case 'critical':
      return 'bg-red-500/20 text-red-300';
    case 'warning':
      return 'bg-amber-500/20 text-amber-300';
    case 'info':
    default:
      return 'bg-blue-500/20 text-blue-300';
  }
}

export function statusBadgeClass(status: string): string {
  return status === 'acknowledged'
    ? 'bg-green-500/20 text-green-300'
    : 'bg-gray-500/20 text-gray-300';
}

const SAFE_METADATA_KEYS = new Set(['health_category', 'strikes', 'checked_at', 'truncated']);

export function safeMetadataEntries(metadata: Record<string, unknown> | undefined): [string, string][] {
  if (!metadata || typeof metadata !== 'object') return [];
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(metadata)) {
    if (!SAFE_METADATA_KEYS.has(key)) continue;
    if (value == null) continue;
    const text = String(value);
    if (/password|token|rtsp:\/\/|mongodb:\/\//i.test(text)) continue;
    out.push([key, text]);
  }
  return out;
}

export function formatOccurredAt(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
