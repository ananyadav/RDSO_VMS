import React from 'react';
import toast from 'react-hot-toast';
import { Play, Pause, Plus, Minus, RotateCw } from 'lucide-react';
import Card from './Card';

const Section = ({ title, children }) => (
  <div className="py-4 border-b border-gray-700">
    <h4 className="text-sm font-semibold text-gray-400 mb-3">{title}</h4>
    {children}
  </div>
);

export default function PTZPresets({ presets, currentPreset, onPresetChange, onRecall }) {
  return (
    <Card>
      <Section title="Presets">
        <select value={currentPreset} onChange={(e) => onPresetChange(e.target.value)} className="input-style w-full mb-2">
          {presets.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <div className="grid grid-cols-3 gap-2">
          <button onClick={() => onRecall(currentPreset)} className="bg-gray-700 hover:bg-blue-600 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium">
            <RotateCw size={14} /> Recall
          </button>
          <button onClick={() => toast('Set Preset (demo)')} className="bg-gray-700 hover:bg-blue-600 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium">
            <Plus size={14} /> Set
          </button>
          <button onClick={() => toast('Remove Preset (demo)')} className="bg-gray-700 hover:bg-blue-600 text-white rounded-md flex items-center justify-center gap-1 h-10 px-2 transition-colors text-sm font-medium">
            <Minus size={14} /> Remove
          </button>
        </div>
      </Section>
      
      <Section title="Speed">
        <div className="grid grid-cols-3 gap-2">
          <button className="bg-gray-700 hover:bg-blue-600 active:bg-blue-700 text-white rounded-md flex items-center justify-center h-10 w-10 transition-colors text-sm font-medium">1</button>
          <button className="bg-gray-700 hover:bg-blue-600 active:bg-blue-700 text-white rounded-md flex items-center justify-center h-10 w-10 transition-colors text-sm font-medium">2</button>
          <button className="bg-gray-700 hover:bg-blue-600 active:bg-blue-700 text-white rounded-md flex items-center justify-center h-10 w-10 transition-colors text-sm font-medium">3</button>
        </div>
      </Section>

      <div className="pt-4">
        <h4 className="text-sm font-semibold text-gray-400 mb-3">Patrol</h4>
        <div className="flex flex-col gap-2">
          <button className="bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white rounded-md flex items-center justify-center gap-2 h-10 transition-colors text-sm font-medium">
            <Play size={16} /> Start Patrol
          </button>
          <button className="bg-gray-700 hover:bg-blue-600 active:bg-blue-700 text-white rounded-md flex items-center justify-center gap-2 h-10 transition-colors text-sm font-medium">
            <Pause size={16} /> Stop Patrol
          </button>
        </div>
      </div>
    </Card>
  );
}
