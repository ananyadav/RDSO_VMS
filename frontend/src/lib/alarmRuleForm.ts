import type { AlarmRulePayload } from './alarmRulesApi';
import { ACTIVE_TRIGGER, type AlarmAction, type AlarmSeverity, RECORDING_DURATION_DEFAULT, RECORDING_DURATION_MAX, RECORDING_DURATION_MIN } from './alarmRuleLabels';

export const COOLDOWN_MIN = 0;
export const COOLDOWN_MAX = 86400;

export interface AlarmRuleFormValues {
  name: string;
  camera_id: string;
  source_type: string;
  severity: AlarmSeverity;
  actions: AlarmAction[];
  cooldown_seconds: number;
  enabled: boolean;
  recording_duration_seconds: number;
}

export interface AlarmRuleFormErrors {
  name?: string;
  camera_id?: string;
  source_type?: string;
  actions?: string;
  cooldown_seconds?: string;
  recording_duration_seconds?: string;
}

export function defaultAlarmRuleFormValues(): AlarmRuleFormValues {
  return {
    name: '',
    camera_id: '',
    source_type: ACTIVE_TRIGGER,
    severity: 'warning',
    actions: ['create_event', 'ui_notification'],
    cooldown_seconds: 60,
    enabled: true,
    recording_duration_seconds: RECORDING_DURATION_DEFAULT,
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

  if (values.source_type !== ACTIVE_TRIGGER) {
    errors.source_type = 'Only Signal Loss is available today';
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
    if (
      !Number.isInteger(values.recording_duration_seconds) ||
      values.recording_duration_seconds < RECORDING_DURATION_MIN ||
      values.recording_duration_seconds > RECORDING_DURATION_MAX
    ) {
      errors.recording_duration_seconds = `Recording duration must be ${RECORDING_DURATION_MIN}–${RECORDING_DURATION_MAX} seconds`;
    }
  }

  return errors;
}

export function formValuesToPayload(values: AlarmRuleFormValues): AlarmRulePayload {
  const payload: AlarmRulePayload = {
    name: values.name.trim(),
    camera_id: values.camera_id.trim(),
    trigger: { source_type: ACTIVE_TRIGGER },
    severity: values.severity,
    actions: [...values.actions],
    cooldown_seconds: values.cooldown_seconds,
    enabled: values.enabled,
  };
  if (values.actions.includes('start_recording')) {
    payload.recording = { duration_seconds: values.recording_duration_seconds };
  }
  return payload;
}

export function hasFormErrors(errors: AlarmRuleFormErrors): boolean {
  return Object.keys(errors).length > 0;
}
