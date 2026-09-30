import type { AlarmRulePayload } from './alarmRulesApi';
import {
  ACTIVE_TRIGGER,
  CONFIGURABLE_TRIGGERS,
  type AlarmAction,
  type AlarmSeverity,
  POST_ALARM_DEFAULT,
  POST_ALARM_MAX,
  POST_ALARM_MIN,
  PRE_ALARM_DEFAULT,
  PRE_ALARM_MAX,
  PRE_ALARM_MIN,
  RECORDING_DURATION_DEFAULT,
} from './alarmRuleLabels';
import { DEFAULT_ALARM_PRIORITY, priorityFromSeverity } from './priorityLevels';

export const COOLDOWN_MIN = 0;
export const COOLDOWN_MAX = 86400;

export interface AlarmRuleFormValues {
  name: string;
  camera_id: string;
  source_type: string;
  severity: AlarmSeverity;
  priority: number;
  actions: AlarmAction[];
  cooldown_seconds: number;
  enabled: boolean;
  /** @deprecated Prefer post_alarm; kept for form binding compatibility */
  recording_duration_seconds: number;
  pre_alarm_seconds: number;
  post_alarm_seconds: number;
  /** RDSO 18.2.28 — optional Live View layout switch (with ui_notification). */
  display_enabled: boolean;
  display_monitor_id: number;
  display_layout: string;
  display_slot: number;
  display_restore_on_reset: boolean;
}

export interface AlarmRuleFormErrors {
  name?: string;
  camera_id?: string;
  source_type?: string;
  actions?: string;
  cooldown_seconds?: string;
  recording_duration_seconds?: string;
  pre_alarm_seconds?: string;
  post_alarm_seconds?: string;
  display_slot?: string;
}

export function defaultAlarmRuleFormValues(): AlarmRuleFormValues {
  return {
    name: '',
    camera_id: '',
    source_type: ACTIVE_TRIGGER,
    severity: 'warning',
    priority: priorityFromSeverity('warning'),
    actions: ['create_event', 'ui_notification'],
    cooldown_seconds: 60,
    enabled: true,
    recording_duration_seconds: RECORDING_DURATION_DEFAULT,
    pre_alarm_seconds: PRE_ALARM_DEFAULT,
    post_alarm_seconds: POST_ALARM_DEFAULT,
    display_enabled: false,
    display_monitor_id: 1,
    display_layout: '2x2',
    display_slot: 0,
    display_restore_on_reset: true,
  };
}

export function validateAlarmRuleForm(values: AlarmRuleFormValues): AlarmRuleFormErrors {
  const errors: AlarmRuleFormErrors = {};
  const name = values.name.trim();
  if (!name) {
    errors.name = 'Rule name is required';
  } else if (name.length > 120) {
    errors.name = 'Rule name must be at most 120 characters';
  }

  if (!values.camera_id.trim()) {
    errors.camera_id = 'Camera is required';
  }

  if (!CONFIGURABLE_TRIGGERS.has(values.source_type)) {
    errors.source_type = 'Select Signal Loss, Motion, or Digital Input / Relay';
  }

  if (!values.actions.length) {
    errors.actions = 'Select at least one action';
  }

  if (
    !Number.isInteger(values.cooldown_seconds) ||
    values.cooldown_seconds < COOLDOWN_MIN ||
    values.cooldown_seconds > COOLDOWN_MAX
  ) {
    errors.cooldown_seconds = `Cooldown must be ${COOLDOWN_MIN}–${COOLDOWN_MAX} seconds`;
  }

  if (values.actions.includes('start_recording')) {
    const post = values.post_alarm_seconds ?? values.recording_duration_seconds;
    if (!Number.isInteger(post) || post < POST_ALARM_MIN || post > POST_ALARM_MAX) {
      errors.post_alarm_seconds = `Post-alarm must be ${POST_ALARM_MIN}–${POST_ALARM_MAX} seconds`;
      errors.recording_duration_seconds = errors.post_alarm_seconds;
    }
    if (
      !Number.isInteger(values.pre_alarm_seconds) ||
      values.pre_alarm_seconds < PRE_ALARM_MIN ||
      values.pre_alarm_seconds > PRE_ALARM_MAX
    ) {
      errors.pre_alarm_seconds = `Pre-alarm must be ${PRE_ALARM_MIN}–${PRE_ALARM_MAX} seconds`;
    }
  }

  if (values.display_enabled && values.actions.includes('ui_notification')) {
    const cols = Number(String(values.display_layout).split('x')[0]) || 2;
    const maxSlot = cols * cols - 1;
    if (
      !Number.isInteger(values.display_slot) ||
      values.display_slot < 0 ||
      values.display_slot > maxSlot
    ) {
      errors.display_slot = `Slot must be 0–${maxSlot} for ${values.display_layout}`;
    }
  }

  return errors;
}

export function formValuesToPayload(values: AlarmRuleFormValues): AlarmRulePayload {
  const sourceType = CONFIGURABLE_TRIGGERS.has(values.source_type)
    ? values.source_type
    : ACTIVE_TRIGGER;
  const payload: AlarmRulePayload = {
    name: values.name.trim(),
    camera_id: values.camera_id.trim(),
    trigger: { source_type: sourceType },
    severity: values.severity,
    priority: values.priority || DEFAULT_ALARM_PRIORITY,
    actions: [...values.actions],
    cooldown_seconds: values.cooldown_seconds,
    enabled: values.enabled,
  };
  if (values.actions.includes('start_recording')) {
    const post = values.post_alarm_seconds || values.recording_duration_seconds;
    payload.recording = {
      pre_alarm_seconds: values.pre_alarm_seconds,
      post_alarm_seconds: post,
      duration_seconds: post,
    };
  }
  if (values.actions.includes('ui_notification') && values.display_enabled) {
    payload.display = {
      mode: 'layout_switch',
      monitor_id: values.display_monitor_id,
      layout: values.display_layout,
      slot: values.display_slot,
      restore_on_reset: values.display_restore_on_reset,
    };
  } else {
    payload.display = null;
  }
  return payload;
}

export function hasFormErrors(errors: AlarmRuleFormErrors): boolean {
  return Object.keys(errors).length > 0;
}
