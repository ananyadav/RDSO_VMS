import React from 'react';
import { Play, RotateCw, Plus, Minus } from 'lucide-react';
import Card from './Card';
import type { PtzPreset } from '../lib/ptzApi';

const Section = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <div className="py-4 border-b border-gray-700">
    <h4 className="text-sm font-semibold text-gray-400 mb-3">{title}</h4>
    {children}
  </div>
);

interface PTZPresetsProps {
  presets: PtzPreset[];
  selectedPresetId: number | null;
  onPresetChange: (presetId: number) => void;
  onRecall: () => void;
  onSet: () => void;
  onRemove: () => void;
  disabled?: boolean;
  loading?: boolean;
}

export default function PTZPresets({
  presets,
  selectedPresetId,
  onPresetChange,
  onRecall,
  onSet,
  onRemove,
  disabled = false,
  loading = false,
}: PTZPresetsProps) {
  return (
    <Card>
      <Section title="Presets">
        {loading ? (
          <p className="text-xs text-gray-500">Loading presets from camera…</p>
        ) : presets.length === 0 ? (
          <>
            <p className="text-xs text-gray-500 mb-2">No presets saved yet — pick a slot and use Set.</p>
            <select
              value={selectedPresetId ?? 1}
              onChange={(e) => onPresetChange(Number(e.target.value))}
              disabled={disabled}
              className="input-style w-full mb-2"
            >
              {[1, 2, 3, 4, 5, 6, 7, 8].map((id) => (
                <option key={id} value={id}>
                  Slot {id}
                </option>
              ))}
            </select>
          </>
        ) : null}
        {presets.length > 0 && (
        <select
          value={selectedPresetId ?? ''}
          onChange={(e) => onPresetChange(Number(e.target.value))}
          disabled={disabled}
          className="input-style w-full mb-2"
        >
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}: {p.name}
            </option>
          ))}
        </select>
        )}
        <div className="grid grid-cols-3 gap-2">
          <button
            type="button"
            disabled={disabled || selectedPresetId == null}
            onClick={onRecall}
            className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium"
          >
            <RotateCw size={14} /> Recall
          </button>
          <button
            type="button"
            disabled={disabled || selectedPresetId == null}
            onClick={onSet}
            className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium"
          >
            <Plus size={14} /> Set
          </button>
          <button
            type="button"
            disabled={disabled || selectedPresetId == null}
            onClick={onRemove}
            className="bg-gray-700 hover:bg-red-700 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium"
          >
            <Minus size={14} /> Remove
          </button>
        </div>
      </Section>

      <Section title="Speed">
        <p className="text-xs text-gray-500">Use speed buttons in the header (1 = slow, 3 = fast).</p>
      </Section>

      <div className="pt-4">
        <h4 className="text-sm font-semibold text-gray-400 mb-3">Patrol</h4>
        <p className="text-xs text-gray-500 flex items-center gap-1">
          <Play size={14} /> Configure patrol on the camera/NVR directly (not via this UI yet).
        </p>
      </div>
    </Card>
  );
}
