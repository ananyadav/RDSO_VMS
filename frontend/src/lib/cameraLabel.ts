/** User-facing camera label for live tiles and player overlays. */

export interface CameraLabelSource {
  displayName?: string;
  name?: string;
  ip_address?: string;
  ipAddress?: string;
  cameraUid?: string;
}

/** displayName || name || ip_address (never camera_group / location_path). */
export function cameraTileLabel(camera: CameraLabelSource | null | undefined): string {
  if (!camera) return 'Camera';

  const displayName = camera.displayName?.trim();
  if (displayName) return displayName;

  const name = camera.name?.trim();
  if (name) return name;

  const ip = (camera.ip_address || camera.ipAddress || '').trim();
  if (ip) return ip;

  const uid = camera.cameraUid || '';
  const fromUid = uid.match(/^ip_(\d+)_(\d+)_(\d+)_(\d+)$/);
  if (fromUid) return fromUid.slice(1).join('.');

  return 'Camera';
}
