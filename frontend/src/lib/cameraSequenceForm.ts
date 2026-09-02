import type { CameraSequence, CameraSequencePayload } from './cameraSequencesApi';

export const DWELL_MIN_SECONDS = 2;
export const DWELL_MAX_SECONDS = 300;
export const MIN_SEQUENCE_CAMERAS = 2;

export interface CameraSequenceFormValues {
  name: string;
  description: string;
  enabled: boolean;
  camera_ids: string[];
  dwell_seconds: number;
}

export interface CameraSequenceFormErrors {
  name?: string;
  camera_ids?: string;
  dwell_seconds?: string;
}

export function defaultCameraSequenceFormValues(): CameraSequenceFormValues {
  return {
    name: '',
    description: '',
    enabled: true,
    camera_ids: [],
    dwell_seconds: 10,
  };
}

export function formValuesFromSequence(sequence: CameraSequence): CameraSequenceFormValues {
  return {
    name: sequence.name,
    description: sequence.description || '',
    enabled: sequence.enabled,
    camera_ids: [...sequence.camera_ids],
    dwell_seconds: sequence.dwell_seconds,
  };
}

export function validateCameraSequenceForm(
  values: CameraSequenceFormValues,
): CameraSequenceFormErrors {
  const errors: CameraSequenceFormErrors = {};
  const name = values.name.trim();
  if (!name) {
    errors.name = 'Name is required';
  }
  if (values.camera_ids.length < MIN_SEQUENCE_CAMERAS) {
    errors.camera_ids = `Select at least ${MIN_SEQUENCE_CAMERAS} cameras`;
  }
  const unique = new Set(values.camera_ids);
  if (unique.size !== values.camera_ids.length) {
    errors.camera_ids = 'Duplicate cameras are not allowed';
  }
  if (
    !Number.isFinite(values.dwell_seconds) ||
    values.dwell_seconds < DWELL_MIN_SECONDS ||
    values.dwell_seconds > DWELL_MAX_SECONDS
  ) {
    errors.dwell_seconds = `Dwell time must be between ${DWELL_MIN_SECONDS} and ${DWELL_MAX_SECONDS} seconds`;
  }
  return errors;
}

export function hasFormErrors(errors: CameraSequenceFormErrors): boolean {
  return Object.values(errors).some(Boolean);
}

export function formValuesToPayload(values: CameraSequenceFormValues): CameraSequencePayload {
  return {
    name: values.name.trim(),
    description: values.description.trim(),
    enabled: values.enabled,
    camera_ids: [...values.camera_ids],
    dwell_seconds: Math.floor(values.dwell_seconds),
  };
}

export function addCameraToSequence(selected: string[], cameraId: string): string[] {
  if (selected.includes(cameraId)) return selected;
  return [...selected, cameraId];
}

export function removeCameraFromSequence(selected: string[], cameraId: string): string[] {
  return selected.filter((id) => id !== cameraId);
}

export function moveCameraInSequence(selected: string[], index: number, direction: -1 | 1): string[] {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= selected.length) return selected;
  const next = [...selected];
  const [item] = next.splice(index, 1);
  next.splice(nextIndex, 0, item);
  return next;
}
