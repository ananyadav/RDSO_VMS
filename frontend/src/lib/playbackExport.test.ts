import { describe, expect, it } from 'vitest';
import { buildExportBody, exportFilenameFromDisposition } from './playbackExport';

describe('playbackExport', () => {
  it('builds export body with camera list and interval', () => {
    expect(
      buildExportBody({
        cameraRefs: ['cam_a', 'cam_b'],
        startIso: '2026-06-08T10:00:00.000Z',
        endIso: '2026-06-08T10:05:00.000Z',
      }),
    ).toEqual({
      cameraUids: ['cam_a', 'cam_b'],
      start: '2026-06-08T10:00:00.000Z',
      end: '2026-06-08T10:05:00.000Z',
    });
  });

  it('parses content-disposition filename', () => {
    expect(
      exportFilenameFromDisposition('attachment; filename="playback-export-20260608.zip"'),
    ).toBe('playback-export-20260608.zip');
  });
});
