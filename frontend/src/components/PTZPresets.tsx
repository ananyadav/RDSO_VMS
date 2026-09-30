import React, { useMemo, useState } from 'react';
import { Plus, Minus, RotateCw } from 'lucide-react';
import Card from './Card';
import type { PtzPreset } from '../lib/ptzApi';

const Section = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <div className="py-2">
    <h4 className="text-sm font-semibold text-gray-400 mb-2">{title}</h4>
    {children}
  </div>
);

interface PTZPresetsProps {
  presets: PtzPreset[];
  selectedPresetId: number | null;
  onPresetChange: (presetId: number) => void;
  onRecall: () => void;
  onSet: (name: string) => void;
  onRemove: () => void;
  disabled?: boolean;
  loading?: boolean;
  supported?: boolean;
  error?: string | null;
  canSet?: boolean;
  canGoto?: boolean;
  canDelete?: boolean;
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
  supported = true,
  error = null,
  canSet = true,
  canGoto = true,
  canDelete = true,
}: PTZPresetsProps) {
  const usedIds = useMemo(() => new Set(presets.map((p) => p.id)), [presets]);
  const slotOptions = useMemo(() => {
    const ids = new Set<number>();
    for (const p of presets) ids.add(p.id);
    for (let i = 1; i <= 8; i += 1) ids.add(i);
    return Array.from(ids).sort((a, b) => a - b);
  }, [presets]);

  const selected = presets.find((p) => p.id === selectedPresetId);
  const [nameDraft, setNameDraft] = useState('');

  const displayName =
    nameDraft || selected?.name || (selectedPresetId != null ? `Preset ${selectedPresetId}` : '');

  if (!supported) {
    return (
      <Card className="!p-3">
        <Section title="Presets">
          <p className="text-xs text-amber-300">
            Presets are not supported by this camera/protocol.
          </p>
          {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
        </Section>
      </Card>
    );
  }

  return (
    <Card className="!p-3">
      <Section title="Presets">
        {loading ? (
          <p className="text-xs text-gray-500">Loading presets from camera…</p>
        ) : (
          <>
            {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
            {presets.length === 0 && (
              <p className="text-xs text-gray-500 mb-2">
                No presets saved yet — pick a slot, name it, and Set at the current position.
              </p>
            )}
            <select
              value={selectedPresetId ?? ''}
              onChange={(e) => {
                setNameDraft('');
                onPresetChange(Number(e.target.value));
              }}
              disabled={disabled}
              className="input-style w-full mb-2"
              aria-label="Preset slot"
            >
              {slotOptions.map((id) => {
                const preset = presets.find((p) => p.id === id);
                return (
                  <option key={id} value={id}>
                    {preset ? `${id}: ${preset.name}` : `Slot ${id}${usedIds.has(id) ? '' : ' (empty)'}`}
                  </option>
                );
              })}
            </select>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setNameDraft(e.target.value)}
              disabled={disabled || !canSet}
              className="input-style w-full mb-2"
              placeholder="Preset name"
              aria-label="Preset name"
            />
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                disabled={disabled || !canGoto || selectedPresetId == null || !selected}
                onClick={onRecall}
                className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 px-2 transition-colors text-sm font-medium"
              >
                <RotateCw size={14} /> Recall
              </button>
              <button
                type="button"
                disabled={disabled || !canSet || selectedPresetId == null}
                onClick={() => onSet(displayName.trim() || `Preset ${selectedPresetId}`)}
                className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 px-2 transition-colors text-sm font-medium"
              >
                <Plus size={14} /> Set
              </button>
              <button
                type="button"
                disabled={disabled || !canDelete || selectedPresetId == null || !selected}
                onClick={onRemove}
                className="bg-gray-700 hover:bg-red-700 disabled:opacity-40 text-white rounded-md flex items-center justify-center gap-1 h-9 px-2 transition-colors text-sm font-medium"
              >
                <Minus size={14} /> Remove
              </button>
            </div>
          </>
        )}
      </Section>
    </Card>
  );
}
