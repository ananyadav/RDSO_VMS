import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2, X } from 'lucide-react';
import type { CameraSequence } from '../lib/cameraSequencesApi';
import {
  addCameraToSequence,
  defaultCameraSequenceFormValues,
  DWELL_MAX_SECONDS,
  DWELL_MIN_SECONDS,
  formValuesFromSequence,
  formValuesToPayload,
  hasFormErrors,
  moveCameraInSequence,
  removeCameraFromSequence,
  validateCameraSequenceForm,
  type CameraSequenceFormValues,
} from '../lib/cameraSequenceForm';

export interface SequenceCameraOption {
  id: string;
  label: string;
  ip_address?: string;
}

interface CameraSequenceModalProps {
  open: boolean;
  sequence: CameraSequence | null;
  cameras: SequenceCameraOption[];
  camerasLoading: boolean;
  saving: boolean;
  onClose: () => void;
  onSubmit: (payload: ReturnType<typeof formValuesToPayload>) => Promise<void>;
}

export default function CameraSequenceModal({
  open,
  sequence,
  cameras,
  camerasLoading,
  saving,
  onClose,
  onSubmit,
}: CameraSequenceModalProps): React.ReactElement | null {
  const [values, setValues] = useState<CameraSequenceFormValues>(defaultCameraSequenceFormValues());
  const [search, setSearch] = useState('');
  const [errors, setErrors] = useState<ReturnType<typeof validateCameraSequenceForm>>({});

  useEffect(() => {
    if (!open) return;
    setValues(sequence ? formValuesFromSequence(sequence) : defaultCameraSequenceFormValues());
    setSearch('');
    setErrors({});
  }, [open, sequence]);

  const cameraById = useMemo(() => {
    const map = new Map<string, SequenceCameraOption>();
    for (const cam of cameras) map.set(cam.id, cam);
    return map;
  }, [cameras]);

  const selectedSet = useMemo(() => new Set(values.camera_ids), [values.camera_ids]);

  const availableCameras = useMemo(() => {
    const q = search.trim().toLowerCase();
    return cameras.filter((cam) => {
      if (selectedSet.has(cam.id)) return false;
      if (!q) return true;
      const haystack = `${cam.label} ${cam.ip_address || ''}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [cameras, search, selectedSet]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const nextErrors = validateCameraSequenceForm(values);
    setErrors(nextErrors);
    if (hasFormErrors(nextErrors)) return;
    await onSubmit(formValuesToPayload(values));
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            {sequence ? 'Edit Camera Sequence' : 'Add Camera Sequence'}
          </h2>
          <button type="button" onClick={onClose} className="p-1 text-gray-500 hover:text-gray-900 dark:hover:text-white">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="flex-1 overflow-y-auto p-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input
                type="text"
                value={values.name}
                onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
                className="input w-full"
                maxLength={120}
                required
              />
              {errors.name && <p className="text-xs text-red-500 mt-1">{errors.name}</p>}
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Dwell Time</label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={DWELL_MIN_SECONDS}
                  max={DWELL_MAX_SECONDS}
                  value={values.dwell_seconds}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, dwell_seconds: Number(e.target.value) || DWELL_MIN_SECONDS }))
                  }
                  className="input w-24"
                />
                <span className="text-sm text-gray-500 dark:text-gray-400">seconds</span>
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                Time each camera remains displayed before switching to the next camera.
              </p>
              {errors.dwell_seconds && <p className="text-xs text-red-500 mt-1">{errors.dwell_seconds}</p>}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
            <textarea
              value={values.description}
              onChange={(e) => setValues((v) => ({ ...v, description: e.target.value }))}
              className="input w-full min-h-[72px]"
              maxLength={500}
            />
          </div>

          <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={values.enabled}
              onChange={(e) => setValues((v) => ({ ...v, enabled: e.target.checked }))}
              className="rounded border-gray-400"
            />
            Enabled
          </label>

          <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">Ordered Camera List</h3>
            {errors.camera_ids && <p className="text-xs text-red-500 mb-2">{errors.camera_ids}</p>}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 min-h-[240px]">
              <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex flex-col">
                <div className="px-3 py-2 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
                  <p className="text-xs font-semibold uppercase text-gray-500">Available Cameras</p>
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search name or IP…"
                    className="input w-full mt-2 text-sm"
                  />
                </div>
                <div className="flex-1 overflow-y-auto max-h-56 p-2 space-y-1">
                  {camerasLoading && (
                    <div className="flex items-center gap-2 text-sm text-gray-500 p-2">
                      <Loader2 className="animate-spin" size={16} /> Loading cameras…
                    </div>
                  )}
                  {!camerasLoading && availableCameras.length === 0 && (
                    <p className="text-xs text-gray-500 p-2">No cameras match your search.</p>
                  )}
                  {availableCameras.map((cam) => (
                    <div
                      key={cam.id}
                      className="flex items-center justify-between gap-2 px-2 py-1.5 rounded border border-gray-200 dark:border-gray-700"
                    >
                      <div className="min-w-0">
                        <p className="text-sm truncate">{cam.label}</p>
                        {cam.ip_address && (
                          <p className="text-[10px] text-gray-500 font-mono truncate">{cam.ip_address}</p>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          setValues((v) => ({
                            ...v,
                            camera_ids: addCameraToSequence(v.camera_ids, cam.id),
                          }))
                        }
                        className="btn-secondary px-2 py-1 text-xs w-auto shrink-0"
                      >
                        Add
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex flex-col">
                <div className="px-3 py-2 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-200 dark:border-gray-700">
                  <p className="text-xs font-semibold uppercase text-gray-500">Selected Cameras (in order)</p>
                </div>
                <div className="flex-1 overflow-y-auto max-h-56 p-2 space-y-1">
                  {values.camera_ids.length === 0 && (
                    <p className="text-xs text-gray-500 p-2">Add at least two cameras to the sequence.</p>
                  )}
                  {values.camera_ids.map((cameraId, index) => {
                    const cam = cameraById.get(cameraId);
                    return (
                      <div
                        key={cameraId}
                        className="flex items-center gap-2 px-2 py-1.5 rounded border border-emerald-500/30 bg-emerald-500/5"
                      >
                        <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400 w-5 shrink-0">
                          {index + 1}.
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm truncate">{cam?.label || cameraId}</p>
                          {cam?.ip_address && (
                            <p className="text-[10px] text-gray-500 font-mono truncate">{cam.ip_address}</p>
                          )}
                        </div>
                        <div className="flex items-center gap-0.5 shrink-0">
                          <button
                            type="button"
                            disabled={index === 0}
                            onClick={() =>
                              setValues((v) => ({
                                ...v,
                                camera_ids: moveCameraInSequence(v.camera_ids, index, -1),
                              }))
                            }
                            className="p-1 text-gray-500 hover:text-gray-900 dark:hover:text-white disabled:opacity-30"
                            title="Move up"
                          >
                            <ChevronUp size={16} />
                          </button>
                          <button
                            type="button"
                            disabled={index === values.camera_ids.length - 1}
                            onClick={() =>
                              setValues((v) => ({
                                ...v,
                                camera_ids: moveCameraInSequence(v.camera_ids, index, 1),
                              }))
                            }
                            className="p-1 text-gray-500 hover:text-gray-900 dark:hover:text-white disabled:opacity-30"
                            title="Move down"
                          >
                            <ChevronDown size={16} />
                          </button>
                          <button
                            type="button"
                            onClick={() =>
                              setValues((v) => ({
                                ...v,
                                camera_ids: removeCameraFromSequence(v.camera_ids, cameraId),
                              }))
                            }
                            className="p-1 text-gray-500 hover:text-red-500"
                            title="Remove"
                          >
                            <X size={16} />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </form>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-gray-200 dark:border-gray-700">
          <button type="button" onClick={onClose} disabled={saving} className="btn-secondary px-4 py-2 text-sm w-auto">
            Cancel
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={(e) => void handleSubmit(e as unknown as React.FormEvent)}
            className="btn-primary px-4 py-2 text-sm w-auto"
          >
            {saving ? 'Saving…' : sequence ? 'Save Changes' : 'Create Sequence'}
          </button>
        </div>
      </div>
    </div>
  );
}
