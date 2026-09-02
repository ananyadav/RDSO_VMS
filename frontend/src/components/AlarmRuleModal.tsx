import React, { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import type { AlarmRule } from '../lib/alarmRulesApi';
import {
  ACTION_OPTIONS,
  ACTIVE_TRIGGER,
  SEVERITY_OPTIONS,
  TRIGGER_OPTIONS,
} from '../lib/alarmRuleLabels';
import {
  defaultAlarmRuleFormValues,
  formValuesToPayload,
  hasFormErrors,
  validateAlarmRuleForm,
  type AlarmRuleFormValues,
} from '../lib/alarmRuleForm';

export interface CameraOption {
  id: string;
  label: string;
  ip_address?: string;
}

interface AlarmRuleModalProps {
  open: boolean;
  rule?: AlarmRule | null;
  cameras: CameraOption[];
  camerasLoading?: boolean;
  saving?: boolean;
  onClose: () => void;
  onSave: (payload: ReturnType<typeof formValuesToPayload>) => void | Promise<void>;
}

function ruleToFormValues(rule: AlarmRule): AlarmRuleFormValues {
  const actions = rule.actions.filter(
    (a): a is AlarmRuleFormValues['actions'][number] =>
      a === 'create_event' || a === 'ui_notification' || a === 'start_recording',
  );
  return {
    name: rule.name,
    camera_id: rule.camera_id,
    source_type: rule.trigger?.source_type || ACTIVE_TRIGGER,
    severity: (rule.severity as AlarmRuleFormValues['severity']) || 'warning',
    actions,
    cooldown_seconds: rule.cooldown_seconds ?? 60,
    enabled: rule.enabled,
    recording_duration_seconds: rule.recording?.duration_seconds ?? 60,
  };
}

export default function AlarmRuleModal({
  open,
  rule,
  cameras,
  camerasLoading = false,
  saving = false,
  onClose,
  onSave,
}: AlarmRuleModalProps): React.ReactElement | null {
  const isEdit = Boolean(rule);
  const [values, setValues] = useState<AlarmRuleFormValues>(defaultAlarmRuleFormValues);
  const [cameraSearch, setCameraSearch] = useState('');
  const [fieldErrors, setFieldErrors] = useState<ReturnType<typeof validateAlarmRuleForm>>({});

  useEffect(() => {
    if (!open) return;
    setValues(rule ? ruleToFormValues(rule) : defaultAlarmRuleFormValues());
    setCameraSearch('');
    setFieldErrors({});
  }, [open, rule]);

  const filteredCameras = useMemo(() => {
    const q = cameraSearch.trim().toLowerCase();
    if (!q) return cameras;
    return cameras.filter(
      (c) =>
        c.label.toLowerCase().includes(q) ||
        (c.ip_address || '').toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q),
    );
  }, [cameras, cameraSearch]);

  if (!open) return null;

  const toggleAction = (action: AlarmRuleFormValues['actions'][number]) => {
    setValues((prev) => {
      const has = prev.actions.includes(action);
      const next = has ? prev.actions.filter((a) => a !== action) : [...prev.actions, action];
      return { ...prev, actions: next };
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors = validateAlarmRuleForm(values);
    setFieldErrors(errors);
    if (hasFormErrors(errors)) return;
    await onSave(formValuesToPayload(values));
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="w-full max-w-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            {isEdit ? 'Edit Alarm Rule' : 'Create Alarm Rule'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="p-1 text-gray-500 hover:text-gray-900 dark:hover:text-white rounded"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        <form id="alarm-rule-form" onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Rule Name
            </label>
            <input
              type="text"
              value={values.name}
              onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
              className="input-field w-full"
              maxLength={120}
              placeholder="e.g. Camera 41 Signal Loss"
            />
            {fieldErrors.name && <p className="mt-1 text-xs text-red-500">{fieldErrors.name}</p>}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Camera
            </label>
            <input
              type="search"
              value={cameraSearch}
              onChange={(e) => setCameraSearch(e.target.value)}
              placeholder="Search by name or IP…"
              className="input-field w-full mb-2"
            />
            <select
              value={values.camera_id}
              onChange={(e) => setValues((v) => ({ ...v, camera_id: e.target.value }))}
              className="input-field w-full"
              disabled={camerasLoading}
            >
              <option value="">{camerasLoading ? 'Loading cameras…' : 'Select a camera'}</option>
              {filteredCameras.map((cam) => (
                <option key={cam.id} value={cam.id}>
                  {cam.label}
                  {cam.ip_address ? ` (${cam.ip_address})` : ''}
                </option>
              ))}
            </select>
            {fieldErrors.camera_id && (
              <p className="mt-1 text-xs text-red-500">{fieldErrors.camera_id}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Trigger Type
            </label>
            <div className="space-y-2">
              {TRIGGER_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`flex items-center gap-2 text-sm ${
                    opt.available ? 'text-gray-800 dark:text-gray-200' : 'text-gray-400'
                  }`}
                >
                  <input
                    type="radio"
                    name="trigger"
                    value={opt.value}
                    checked={values.source_type === opt.value}
                    disabled={!opt.available}
                    onChange={() =>
                      opt.available && setValues((v) => ({ ...v, source_type: opt.value }))
                    }
                  />
                  <span>
                    {opt.label}
                    {!opt.available && (
                      <span className="ml-2 text-xs text-gray-500">(Not yet available)</span>
                    )}
                  </span>
                </label>
              ))}
            </div>
            {fieldErrors.source_type && (
              <p className="mt-1 text-xs text-red-500">{fieldErrors.source_type}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Severity
            </label>
            <select
              value={values.severity}
              onChange={(e) =>
                setValues((v) => ({
                  ...v,
                  severity: e.target.value as AlarmRuleFormValues['severity'],
                }))
              }
              className="input-field w-full"
            >
              {SEVERITY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Actions
            </label>
            <div className="space-y-2">
              {ACTION_OPTIONS.map((opt) => (
                <label key={opt.value} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={values.actions.includes(opt.value)}
                    onChange={() => toggleAction(opt.value)}
                  />
                  {opt.label}
                </label>
              ))}
            </div>
            {fieldErrors.actions && (
              <p className="mt-1 text-xs text-red-500">{fieldErrors.actions}</p>
            )}
          </div>

          {values.actions.includes('start_recording') && (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Recording Duration (seconds)
              </label>
              <input
                type="number"
                min={5}
                max={3600}
                value={values.recording_duration_seconds}
                onChange={(e) =>
                  setValues((v) => ({
                    ...v,
                    recording_duration_seconds: parseInt(e.target.value, 10) || 60,
                  }))
                }
                className="input-field w-full"
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                Automatically records this camera for the configured duration when the alarm rule is triggered.
              </p>
              {fieldErrors.recording_duration_seconds && (
                <p className="mt-1 text-xs text-red-500">{fieldErrors.recording_duration_seconds}</p>
              )}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Cooldown (seconds)
            </label>
            <input
              type="number"
              min={0}
              max={86400}
              value={values.cooldown_seconds}
              onChange={(e) =>
                setValues((v) => ({
                  ...v,
                  cooldown_seconds: parseInt(e.target.value, 10) || 0,
                }))
              }
              className="input-field w-full"
            />
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Prevents repeated events from the same rule within this period.
            </p>
            {fieldErrors.cooldown_seconds && (
              <p className="mt-1 text-xs text-red-500">{fieldErrors.cooldown_seconds}</p>
            )}
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={values.enabled}
              onChange={(e) => setValues((v) => ({ ...v, enabled: e.target.checked }))}
            />
            Enabled
          </label>
        </form>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-gray-200 dark:border-gray-700">
          <button type="button" onClick={onClose} disabled={saving} className="btn-secondary px-4 py-2 text-sm w-auto">
            Cancel
          </button>
          <button
            type="submit"
            form="alarm-rule-form"
            disabled={saving}
            className="btn-primary px-4 py-2 text-sm w-auto disabled:opacity-50"
          >
            {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Rule'}
          </button>
        </div>
      </div>
    </div>
  );
}
