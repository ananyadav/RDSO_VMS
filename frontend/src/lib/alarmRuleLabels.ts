/** Display labels and constants for alarm rule UI. */

export const ACTIVE_TRIGGER = 'signal_loss' as const;

export type AlarmSeverity = 'info' | 'warning' | 'critical';
export type AlarmAction = 'create_event' | 'ui_notification' | 'start_recording';

export const RECORDING_DURATION_MIN = 5;
export const RECORDING_DURATION_MAX = 3600;
export const RECORDING_DURATION_DEFAULT = 60;

/** Pre-alarm lookback (real buffer footage before trigger). */
export const PRE_ALARM_MIN = 0;
export const PRE_ALARM_MAX = 300;
export const PRE_ALARM_DEFAULT = 10;

/** Post-alarm continue window after trigger (= legacy duration). */
export const POST_ALARM_MIN = RECORDING_DURATION_MIN;
export const POST_ALARM_MAX = RECORDING_DURATION_MAX;
export const POST_ALARM_DEFAULT = RECORDING_DURATION_DEFAULT;

export const SEVERITY_OPTIONS: { value: AlarmSeverity; label: string }[] = [
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
];

export const ACTION_OPTIONS: { value: AlarmAction; label: string }[] = [
  { value: 'create_event', label: 'Create Event' },
  { value: 'ui_notification', label: 'Show UI Notification' },
  { value: 'start_recording', label: 'Start Recording' },
];

export const CONFIGURABLE_TRIGGERS = new Set(['signal_loss', 'motion', 'digital_input']);

export const TRIGGER_OPTIONS = [
  { value: ACTIVE_TRIGGER, label: 'Signal Loss', available: true },
  { value: 'motion', label: 'Motion', available: true },
  { value: 'digital_input', label: 'Digital Input / Relay', available: true },
  { value: 'recording_failure', label: 'Recording Failure', available: false },
] as const;

export function triggerLabel(sourceType: string): string {
  const match = TRIGGER_OPTIONS.find((t) => t.value === sourceType);
  return match?.label ?? sourceType;
}

export function actionLabel(action: string): string {
  const match = ACTION_OPTIONS.find((a) => a.value === action);
  return match?.label ?? action;
}

export function severityLabel(severity: string): string {
  const match = SEVERITY_OPTIONS.find((s) => s.value === severity);
  return match?.label ?? severity;
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
