import { describe, expect, it, vi } from 'vitest';
import {
  addCameraToSequence,
  defaultCameraSequenceFormValues,
  formValuesToPayload,
  hasFormErrors,
  moveCameraInSequence,
  validateCameraSequenceForm,
} from '../lib/cameraSequenceForm';
import { canAccessPath, isOpsAdminUser } from '../lib/permissions';

const CAM_A = '507f1f77bcf86cd799439011';
const CAM_B = '507f1f77bcf86cd799439012';
const CAM_C = '507f1f77bcf86cd799439013';

describe('cameraSequenceForm validation', () => {
  it('requires name and at least 2 cameras', () => {
    const errors = validateCameraSequenceForm({
      ...defaultCameraSequenceFormValues(),
      name: '',
      camera_ids: [CAM_A],
    });
    expect(errors.name).toBeTruthy();
    expect(errors.camera_ids).toContain('2');
    expect(hasFormErrors(errors)).toBe(true);
  });

  it('rejects duplicate camera IDs', () => {
    const errors = validateCameraSequenceForm({
      ...defaultCameraSequenceFormValues(),
      name: 'Patrol',
      camera_ids: [CAM_A, CAM_A],
    });
    expect(errors.camera_ids).toContain('Duplicate');
  });

  it('rejects invalid dwell range', () => {
    const low = validateCameraSequenceForm({
      ...defaultCameraSequenceFormValues(),
      name: 'Patrol',
      camera_ids: [CAM_A, CAM_B],
      dwell_seconds: 1,
    });
    expect(low.dwell_seconds).toBeTruthy();

    const high = validateCameraSequenceForm({
      ...defaultCameraSequenceFormValues(),
      name: 'Patrol',
      camera_ids: [CAM_A, CAM_B],
      dwell_seconds: 301,
    });
    expect(high.dwell_seconds).toBeTruthy();
  });

  it('preserves order A/B/C in payload', () => {
    const payload = formValuesToPayload({
      ...defaultCameraSequenceFormValues(),
      name: 'Route',
      camera_ids: [CAM_A, CAM_B, CAM_C],
      dwell_seconds: 10,
      enabled: true,
    });
    expect(payload.camera_ids).toEqual([CAM_A, CAM_B, CAM_C]);
  });

  it('reorder C/A/B sends correct camera_ids', () => {
    const reordered = moveCameraInSequence(
      moveCameraInSequence([CAM_A, CAM_B, CAM_C], 2, -1),
      1,
      -1,
    );
    expect(reordered).toEqual([CAM_C, CAM_A, CAM_B]);
    const payload = formValuesToPayload({
      ...defaultCameraSequenceFormValues(),
      name: 'Route',
      camera_ids: reordered,
    });
    expect(payload.camera_ids).toEqual([CAM_C, CAM_A, CAM_B]);
  });

  it('prevents duplicate selection via add helper', () => {
    const next = addCameraToSequence([CAM_A, CAM_B], CAM_A);
    expect(next).toEqual([CAM_A, CAM_B]);
  });
});

describe('camera sequences RBAC', () => {
  const admin = { role: 'admin', permissions: [] as string[] };
  const superAdmin = { role: 'super_admin', permissions: [] as string[] };
  const operator = { role: 'operator', permissions: ['Live View'] };
  const viewer = { role: 'viewer', permissions: ['Live View'] };

  it('allows ops admins to access /camera-sequences', () => {
    expect(isOpsAdminUser(admin)).toBe(true);
    expect(isOpsAdminUser(superAdmin)).toBe(true);
    expect(canAccessPath(admin, '/camera-sequences')).toBe(true);
    expect(canAccessPath(superAdmin, '/camera-sequences')).toBe(true);
  });

  it('denies operator and viewer management route', () => {
    expect(canAccessPath(operator, '/camera-sequences')).toBe(false);
    expect(canAccessPath(viewer, '/camera-sequences')).toBe(false);
  });
});

describe('cameraSequencesApi', () => {
  it('loads sequences from GET /api/camera-sequences without mock data', async () => {
    const sample = {
      items: [
        {
          id: '507f1f77bcf86cd799439099',
          name: 'Main Gate Patrol',
          description: '',
          enabled: true,
          camera_ids: [CAM_A, CAM_B, CAM_C],
          dwell_seconds: 10,
        },
      ],
      total: 1,
      limit: 100,
      offset: 0,
    };

    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(sample), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const { listCameraSequences } = await import('../lib/cameraSequencesApi');
    const data = await listCameraSequences();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/camera-sequences'),
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(data.items).toHaveLength(1);
    expect(data.items[0].camera_ids).toEqual([CAM_A, CAM_B, CAM_C]);

    vi.unstubAllGlobals();
  });

  it('does not treat failed create as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: 'camera_ids must contain at least 2 cameras' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    const { createCameraSequence, CameraSequencesRequestError } = await import('../lib/cameraSequencesApi');
    await expect(
      createCameraSequence({
        name: 'Bad',
        description: '',
        enabled: true,
        camera_ids: [CAM_A],
        dwell_seconds: 10,
      }),
    ).rejects.toBeInstanceOf(CameraSequencesRequestError);

    vi.unstubAllGlobals();
  });

  it('create sequence sends ordered camera_ids', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body.camera_ids).toEqual([CAM_A, CAM_B, CAM_C]);
      return new Response(
        JSON.stringify({
          id: '507f1f77bcf86cd799439099',
          ...body,
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const { createCameraSequence } = await import('../lib/cameraSequencesApi');
    const created = await createCameraSequence({
      name: 'RDSO Camera Sequence Test',
      description: '',
      enabled: true,
      camera_ids: [CAM_A, CAM_B, CAM_C],
      dwell_seconds: 10,
    });
    expect(created.camera_ids).toEqual([CAM_A, CAM_B, CAM_C]);

    vi.unstubAllGlobals();
  });

  it('update enable/disable uses PUT', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(init?.method).toBe('PUT');
      const body = JSON.parse(String(init?.body));
      expect(body.enabled).toBe(false);
      return new Response(
        JSON.stringify({
          id: '507f1f77bcf86cd799439099',
          name: 'Patrol',
          description: '',
          enabled: false,
          camera_ids: [CAM_A, CAM_B],
          dwell_seconds: 15,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const { updateCameraSequence } = await import('../lib/cameraSequencesApi');
    const updated = await updateCameraSequence('507f1f77bcf86cd799439099', { enabled: false });
    expect(updated.enabled).toBe(false);
    expect(updated.dwell_seconds).toBe(15);

    vi.unstubAllGlobals();
  });

  it('delete removes sequence only after backend success', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(init?.method).toBe('DELETE');
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const { deleteCameraSequence } = await import('../lib/cameraSequencesApi');
    await expect(deleteCameraSequence('507f1f77bcf86cd799439099')).resolves.toBeUndefined();

    vi.unstubAllGlobals();
  });
});
