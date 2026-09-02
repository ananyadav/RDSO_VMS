import { describe, expect, it, vi } from 'vitest';
import {
  ACTIVE_TRIGGER,
  actionLabel,
  triggerLabel,
} from '../lib/alarmRuleLabels';
import {
  defaultAlarmRuleFormValues,
  formValuesToPayload,
  hasFormErrors,
  validateAlarmRuleForm,
} from '../lib/alarmRuleForm';
import { canAccessPath, isOpsAdminUser } from '../lib/permissions';

describe('alarmRuleForm validation', () => {
  it('requires name, camera, and at least one action', () => {
    const errors = validateAlarmRuleForm({
      ...defaultAlarmRuleFormValues(),
      name: '',
      camera_id: '',
      actions: [],
    });
    expect(errors.name).toBeTruthy();
    expect(errors.camera_id).toBeTruthy();
    expect(errors.actions).toBeTruthy();
    expect(hasFormErrors(errors)).toBe(true);
  });

  it('rejects unsupported trigger types', () => {
    const errors = validateAlarmRuleForm({
      ...defaultAlarmRuleFormValues(),
      name: 'Motion rule',
      camera_id: '507f1f77bcf86cd799439011',
      source_type: 'motion',
    });
    expect(errors.source_type).toContain('Signal Loss');
  });

  it('rejects invalid cooldown', () => {
    const errors = validateAlarmRuleForm({
      ...defaultAlarmRuleFormValues(),
      name: 'Test',
      camera_id: '507f1f77bcf86cd799439011',
      cooldown_seconds: 999999,
    });
    expect(errors.cooldown_seconds).toBeTruthy();
  });

  it('builds signal_loss payload only', () => {
    const payload = formValuesToPayload({
      ...defaultAlarmRuleFormValues(),
      name: 'RDSO Signal Loss Test',
      camera_id: '507f1f77bcf86cd799439011',
      source_type: ACTIVE_TRIGGER,
      severity: 'warning',
      actions: ['create_event', 'ui_notification'],
      cooldown_seconds: 60,
      enabled: true,
      recording_duration_seconds: 60,
    });
    expect(payload.trigger.source_type).toBe('signal_loss');
    expect(payload.actions).toEqual(['create_event', 'ui_notification']);
    expect(payload.cooldown_seconds).toBe(60);
  });

  it('includes recording config when start_recording selected', () => {
    const payload = formValuesToPayload({
      ...defaultAlarmRuleFormValues(),
      name: 'Rec rule',
      camera_id: '507f1f77bcf86cd799439011',
      actions: ['create_event', 'start_recording'],
      recording_duration_seconds: 45,
    });
    expect(payload.recording).toEqual({ duration_seconds: 45 });
  });

  it('requires valid recording duration when start_recording selected', () => {
    const errors = validateAlarmRuleForm({
      ...defaultAlarmRuleFormValues(),
      name: 'Rec',
      camera_id: '507f1f77bcf86cd799439011',
      actions: ['start_recording'],
      recording_duration_seconds: 2,
    });
    expect(errors.recording_duration_seconds).toBeTruthy();
  });
});

describe('alarm rule labels', () => {
  it('labels signal_loss trigger and actions', () => {
    expect(triggerLabel('signal_loss')).toBe('Signal Loss');
    expect(actionLabel('create_event')).toBe('Create Event');
    expect(actionLabel('ui_notification')).toBe('Show UI Notification');
  });
});

describe('alarm rules RBAC', () => {
  const admin = { role: 'admin', permissions: [] as string[] };
  const superAdmin = { role: 'super_admin', permissions: [] as string[] };
  const operator = { role: 'operator', permissions: ['Events', 'Live View'] };
  const viewer = { role: 'viewer', permissions: ['Live View'] };

  it('allows ops admins to access /alarm-rules', () => {
    expect(isOpsAdminUser(admin)).toBe(true);
    expect(isOpsAdminUser(superAdmin)).toBe(true);
    expect(canAccessPath(admin, '/alarm-rules')).toBe(true);
    expect(canAccessPath(superAdmin, '/alarm-rules')).toBe(true);
  });

  it('denies operator and viewer alarm rule management route', () => {
    expect(canAccessPath(operator, '/alarm-rules')).toBe(false);
    expect(canAccessPath(viewer, '/alarm-rules')).toBe(false);
  });
});

describe('alarmRulesApi', () => {
  it('loads rules from GET /api/alarm-rules without mock data', async () => {
    const sample = {
      items: [
        {
          id: '507f1f77bcf86cd799439013',
          name: 'Test',
          enabled: true,
          camera_id: '507f1f77bcf86cd799439011',
          trigger: { source_type: 'signal_loss' },
          actions: ['create_event'],
          severity: 'warning',
          cooldown_seconds: 60,
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

    const { listAlarmRules } = await import('../lib/alarmRulesApi');
    const data = await listAlarmRules();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/alarm-rules'),
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(data.items).toHaveLength(1);
    expect(data.items[0].trigger.source_type).toBe('signal_loss');

    vi.unstubAllGlobals();
  });

  it('does not treat failed create as success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: 'Invalid payload' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    const { createAlarmRule, AlarmRulesRequestError } = await import('../lib/alarmRulesApi');
    await expect(
      createAlarmRule({
        name: 'Bad',
        camera_id: '507f1f77bcf86cd799439011',
        trigger: { source_type: 'signal_loss' },
        severity: 'warning',
        actions: ['create_event'],
        cooldown_seconds: 60,
        enabled: true,
      }),
    ).rejects.toBeInstanceOf(AlarmRulesRequestError);

    vi.unstubAllGlobals();
  });
});
