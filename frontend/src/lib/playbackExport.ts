/** Playback interval export helpers (RDSO 18.1.16 / 18.3.12.2). */

import { MAX_MULTI_PLAYBACK_CAMERAS } from './multiPlayback';

export const MAX_EXPORT_CAMERAS = MAX_MULTI_PLAYBACK_CAMERAS;

export interface PlaybackExportRequest {
  cameraRefs: string[];
  startIso: string;
  endIso: string;
}

export function buildExportBody(req: PlaybackExportRequest): Record<string, unknown> {
  return {
    cameraUids: req.cameraRefs.slice(0, MAX_EXPORT_CAMERAS),
    start: req.startIso,
    end: req.endIso,
  };
}

export function exportFilenameFromDisposition(header: string | null): string {
  if (!header) return 'playback-export.zip';
  const m = /filename="([^"]+)"/i.exec(header);
  return m?.[1] || 'playback-export.zip';
}
