import React, { useMemo, useState } from 'react';
import { Play, Square, Circle, Save, Trash2 } from 'lucide-react';
import Card from './Card';
import type { PtzPattern } from '../lib/ptzApi';

interface PTZPatternsProps {
  patterns: PtzPattern[];
  selectedPatternId: number | null;
  onPatternChange: (patternId: number) => void;
  onSaveName: (patternId: number, name: string) => void;
  onDelete: () => void;
  onStart: () => void;
  onStop: () => void;
  onRecordStart: () => void;
  onRecordStop: () => void;
  disabled?: boolean;
  loading?: boolean;
  supported?: boolean;
  error?: string | null;
  canSet?: boolean;
  canStart?: boolean;
  canStop?: boolean;
  canRecord?: boolean;
  canDelete?: boolean;
}

/**
 * RDSO 18.2.23 — recorded PTZ pan/tilt/zoom patterns.
 * Distinct from Tours/Patrols (preset sequences).
 */
export default function PTZPatterns({
  patterns,
  selectedPatternId,
  onPatternChange,
  onSaveName,
  onDelete,
  onStart,
  onStop,
  onRecordStart,
  onRecordStop,
  disabled = false,
  loading = false,
  supported = true,
  error = null,
  canSet = true,
  canStart = true,
  canStop = true,
  canRecord = true,
  canDelete = true,
}: PTZPatternsProps) {
  const selected = patterns.find((p) => p.id === selectedPatternId) ?? null;
  const [nameDraft, setNameDraft] = useState('');

  const patternSlots = useMemo(() => {
    const ids = new Set<number>(patterns.map((p) => p.id));
    for (let i = 1; i <= 4; i += 1) ids.add(i);
    return Array.from(ids).sort((a, b) => a - b);
  }, [patterns]);

  const name =
    nameDraft || selected?.name || (selectedPatternId != null ? `Pattern ${selectedPatternId}` : 'Pattern');

  const selectPattern = (id: number) => {
    setNameDraft('');
    onPatternChange(id);
  };

  if (!supported) {
    return (
      <Card className="!p-3">
        <h4 className="text-sm font-semibold text-gray-400 mb-2">Patterns</h4>
        <p className="text-xs text-amber-300">
          Recorded PTZ patterns are not supported by this camera/protocol (distinct from tours/patrols).
        </p>
        {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
      </Card>
    );
  }

  return (
    <Card className="!p-3" data-testid="ptz-patterns">
      <h4 className="text-sm font-semibold text-gray-400 mb-1">Patterns</h4>
      <p className="text-[10px] text-gray-500 mb-2">
        Recorded pan/tilt/zoom path — not a tour/patrol (preset list).
      </p>
      {loading ? (
        <p className="text-xs text-gray-500">Loading patterns…</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-1 mb-2">
            {patternSlots.map((id) => {
              const exists = patterns.some((p) => p.id === id);
              const active = selectedPatternId === id;
              return (
                <button
                  key={id}
                  type="button"
                  disabled={disabled}
                  onClick={() => selectPattern(id)}
                  className={`px-2 py-1 rounded text-xs ${
                    active ? 'bg-blue-600 text-white' : exists ? 'bg-gray-700 text-gray-200' : 'bg-gray-800 text-gray-500'
                  }`}
                >
                  {id}
                </button>
              );
            })}
          </div>

          {canSet && (
            <div className="flex gap-1 mb-2">
              <input
                type="text"
                value={name}
                disabled={disabled || selectedPatternId == null}
                onChange={(e) => setNameDraft(e.target.value)}
                className="flex-1 min-w-0 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs text-white"
                placeholder="Pattern name"
                aria-label="Pattern name"
              />
              <button
                type="button"
                title="Save pattern name"
                disabled={disabled || selectedPatternId == null}
                onClick={() => selectedPatternId != null && onSaveName(selectedPatternId, name)}
                className="px-2 py-1 rounded bg-gray-700 hover:bg-blue-600 disabled:opacity-40 text-white"
              >
                <Save size={14} />
              </button>
            </div>
          )}

          <div className="grid grid-cols-2 gap-1.5">
            <button
              type="button"
              disabled={disabled || !canStart || selectedPatternId == null}
              onClick={onStart}
              className="flex items-center justify-center gap-1 px-2 py-1.5 rounded text-xs bg-emerald-800 hover:bg-emerald-700 disabled:opacity-40 text-white"
            >
              <Play size={14} /> Run
            </button>
            <button
              type="button"
              disabled={disabled || !canStop || selectedPatternId == null}
              onClick={onStop}
              className="flex items-center justify-center gap-1 px-2 py-1.5 rounded text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white"
            >
              <Square size={14} /> Stop
            </button>
            {canRecord && (
              <>
                <button
                  type="button"
                  disabled={disabled || selectedPatternId == null}
                  onClick={onRecordStart}
                  className="flex items-center justify-center gap-1 px-2 py-1.5 rounded text-xs bg-amber-800 hover:bg-amber-700 disabled:opacity-40 text-white"
                  title="Start recording operator PTZ moves into this pattern"
                >
                  <Circle size={14} className="fill-current" /> Record
                </button>
                <button
                  type="button"
                  disabled={disabled || selectedPatternId == null}
                  onClick={onRecordStop}
                  className="flex items-center justify-center gap-1 px-2 py-1.5 rounded text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white"
                >
                  <Square size={14} /> End rec
                </button>
              </>
            )}
            {canDelete && (
              <button
                type="button"
                disabled={disabled || selectedPatternId == null}
                onClick={onDelete}
                className="col-span-2 flex items-center justify-center gap-1 px-2 py-1.5 rounded text-xs bg-red-900/70 hover:bg-red-800 disabled:opacity-40 text-white"
              >
                <Trash2 size={14} /> Delete pattern
              </button>
            )}
          </div>
          {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
        </>
      )}
    </Card>
  );
}
