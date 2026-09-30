import React, { useMemo, useState } from 'react';
import { Play, Square, Save, Trash2, ArrowUp, ArrowDown, Plus } from 'lucide-react';
import Card from './Card';
import type { PtzPreset, PtzTour, PtzTourStep } from '../lib/ptzApi';

interface PTZToursProps {
  tours: PtzTour[];
  presets: PtzPreset[];
  selectedTourId: number | null;
  onTourChange: (tourId: number) => void;
  onSave: (tourId: number, name: string, steps: PtzTourStep[]) => void;
  onDelete: () => void;
  onStart: () => void;
  onStop: () => void;
  disabled?: boolean;
  loading?: boolean;
  supported?: boolean;
  error?: string | null;
  canSet?: boolean;
  canStart?: boolean;
  canStop?: boolean;
  canDelete?: boolean;
}

export default function PTZTours({
  tours,
  presets,
  selectedTourId,
  onTourChange,
  onSave,
  onDelete,
  onStart,
  onStop,
  disabled = false,
  loading = false,
  supported = true,
  error = null,
  canSet = true,
  canStart = true,
  canStop = true,
  canDelete = true,
}: PTZToursProps) {
  const selected = tours.find((t) => t.id === selectedTourId) ?? null;
  const [nameDraft, setNameDraft] = useState('');
  const [stepsDraft, setStepsDraft] = useState<PtzTourStep[] | null>(null);

  const tourSlots = useMemo(() => {
    const ids = new Set<number>(tours.map((t) => t.id));
    for (let i = 1; i <= 4; i += 1) ids.add(i);
    return Array.from(ids).sort((a, b) => a - b);
  }, [tours]);

  const steps = stepsDraft ?? selected?.steps ?? [];
  const name =
    nameDraft || selected?.name || (selectedTourId != null ? `Tour ${selectedTourId}` : 'Tour');

  const selectTour = (id: number) => {
    setNameDraft('');
    setStepsDraft(null);
    onTourChange(id);
  };

  const updateSteps = (next: PtzTourStep[]) => setStepsDraft(next);

  if (!supported) {
    return (
      <Card className="!p-3">
        <h4 className="text-sm font-semibold text-gray-400 mb-2">Tours / Patrols</h4>
        <p className="text-xs text-amber-300">
          Tours/patrols are not supported by this camera/protocol.
        </p>
        {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
      </Card>
    );
  }

  return (
    <Card className="!p-3">
      <h4 className="text-sm font-semibold text-gray-400 mb-2">Tours / Patrols</h4>
      {loading ? (
        <p className="text-xs text-gray-500">Loading tours from camera…</p>
      ) : (
        <>
          {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
          <select
            value={selectedTourId ?? ''}
            onChange={(e) => selectTour(Number(e.target.value))}
            disabled={disabled}
            className="input-style w-full mb-2"
            aria-label="Tour slot"
          >
            {tourSlots.map((id) => {
              const tour = tours.find((t) => t.id === id);
              return (
                <option key={id} value={id}>
                  {tour ? `${id}: ${tour.name}` : `Slot ${id} (empty)`}
                </option>
              );
            })}
          </select>
          <input
            type="text"
            value={name}
            onChange={(e) => setNameDraft(e.target.value)}
            disabled={disabled || !canSet}
            className="input-style w-full mb-2"
            placeholder="Tour name"
            aria-label="Tour name"
          />

          <div className="space-y-1.5 mb-2 max-h-40 overflow-y-auto">
            {steps.length === 0 && (
              <p className="text-xs text-gray-500">Add presets to build this tour.</p>
            )}
            {steps.map((step, index) => (
              <div
                key={`${step.presetId}-${index}`}
                className="flex items-center gap-1.5 text-xs bg-gray-800/60 rounded px-2 py-1"
              >
                <span className="text-gray-400 w-4">{index + 1}.</span>
                <select
                  value={step.presetId}
                  disabled={disabled || !canSet}
                  onChange={(e) => {
                    const next = steps.map((s, i) =>
                      i === index ? { ...s, presetId: Number(e.target.value) } : s,
                    );
                    updateSteps(next);
                  }}
                  className="input-style flex-1 !py-1 !text-xs"
                >
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.id}: {p.name}
                    </option>
                  ))}
                  {!presets.some((p) => p.id === step.presetId) && (
                    <option value={step.presetId}>Preset {step.presetId}</option>
                  )}
                </select>
                <input
                  type="number"
                  min={1}
                  max={300}
                  value={step.delay ?? 15}
                  disabled={disabled || !canSet}
                  onChange={(e) => {
                    const delay = Math.max(15, Number(e.target.value) || 15);
                    updateSteps(steps.map((s, i) => (i === index ? { ...s, delay } : s)));
                  }}
                  className="input-style w-16 !py-1 !text-xs"
                  title="Dwell seconds"
                  aria-label={`Dwell seconds step ${index + 1}`}
                />
                <button
                  type="button"
                  disabled={disabled || !canSet || index === 0}
                  onClick={() => {
                    const next = [...steps];
                    [next[index - 1], next[index]] = [next[index], next[index - 1]];
                    updateSteps(next);
                  }}
                  className="p-1 rounded hover:bg-gray-700 disabled:opacity-30"
                  title="Move up"
                >
                  <ArrowUp size={12} />
                </button>
                <button
                  type="button"
                  disabled={disabled || !canSet || index >= steps.length - 1}
                  onClick={() => {
                    const next = [...steps];
                    [next[index], next[index + 1]] = [next[index + 1], next[index]];
                    updateSteps(next);
                  }}
                  className="p-1 rounded hover:bg-gray-700 disabled:opacity-30"
                  title="Move down"
                >
                  <ArrowDown size={12} />
                </button>
                <button
                  type="button"
                  disabled={disabled || !canSet}
                  onClick={() => updateSteps(steps.filter((_, i) => i !== index))}
                  className="p-1 rounded hover:bg-red-800 disabled:opacity-30"
                  title="Remove step"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>

          <button
            type="button"
            disabled={disabled || !canSet || presets.length === 0}
            onClick={() =>
              updateSteps([
                ...steps,
                { presetId: presets[0]?.id ?? 1, delay: 15, speed: 30 },
              ])
            }
            className="w-full mb-2 bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-8 text-xs"
          >
            <Plus size={12} /> Add preset step
          </button>

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={disabled || !canSet || selectedTourId == null || steps.length === 0}
              onClick={() =>
                selectedTourId != null &&
                onSave(selectedTourId, name.trim() || `Tour ${selectedTourId}`, steps)
              }
              className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 text-sm"
            >
              <Save size={14} /> Save
            </button>
            <button
              type="button"
              disabled={disabled || !canDelete || !selected}
              onClick={onDelete}
              className="bg-gray-700 hover:bg-red-700 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 text-sm"
            >
              <Trash2 size={14} /> Delete
            </button>
            <button
              type="button"
              disabled={disabled || !canStart || !selected}
              onClick={onStart}
              className="bg-emerald-800 hover:bg-emerald-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 text-sm"
            >
              <Play size={14} /> Start
            </button>
            <button
              type="button"
              disabled={disabled || !canStop || selectedTourId == null}
              onClick={onStop}
              className="bg-gray-700 hover:bg-amber-700 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 text-sm"
            >
              <Square size={14} /> Stop
            </button>
          </div>
        </>
      )}
    </Card>
  );
}
