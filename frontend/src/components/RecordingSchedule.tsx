import React, { useState, useEffect } from 'react';
import { Video, Save } from 'lucide-react';
import Card from './Card';

// --- The Reusable Toggle Switch Component (no changes here) ---
interface ToggleSwitchProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
}
const ToggleSwitch = ({ enabled, onChange, disabled = false }: ToggleSwitchProps) => (
  <button
    type="button"
    onClick={() => onChange(!enabled)}
    disabled={disabled}
    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800 ${
      enabled ? 'bg-blue-600' : 'bg-gray-600'
    } ${disabled ? 'cursor-not-allowed' : ''}`}
  >
    <span
      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
        enabled ? 'translate-x-5' : 'translate-x-0'
      }`}
    />
  </button>
);

interface Camera {
  id: string;
  name: string;
}

interface RecordingScheduleProps {
  cameras: Camera[];
  schedule: Record<string, boolean>;
  onSave: (newSchedule: Record<string, boolean>) => void;
  isRecordingEnabled: boolean; // NEW PROP
  onToggleRecording: (enabled: boolean) => void; // NEW PROP
}

export default function RecordingSchedule({ cameras, schedule, onSave, isRecordingEnabled, onToggleRecording }: RecordingScheduleProps): React.ReactElement {
  const [localSchedule, setLocalSchedule] = useState(schedule);

  // Keep local state in sync when the parent prop changes (e.g. after fetch)
  useEffect(() => {
    setLocalSchedule(schedule);
  }, [schedule]);

  const handleToggle = (cameraId: string) => {
    setLocalSchedule(prev => ({
      ...prev,
      [cameraId]: !prev[cameraId],
    }));
  };

  return (
    <Card>
      <div className="flex items-center justify-between pb-4 border-b border-gray-700">
        <div className="flex items-center">
          <Video size={18} className="mr-3 text-gray-400" />
          <h3 className="text-lg font-bold text-white">Camera Recording</h3>
        </div>
        <button onClick={() => onSave(localSchedule)} className="btn-primary flex items-center text-sm">
          <Save size={16} className="mr-2" />
          Save Changes
        </button>
      </div>

      {/* --- NEW: Master Toggle Switch Section --- */}
      <div className="flex items-center justify-between py-4">
        <div>
          <span className="font-bold text-white">Recording Active</span>
          <p className="text-xs text-gray-500 mt-0.5">Turn off to stop all cameras immediately</p>
        </div>
        <ToggleSwitch
          enabled={isRecordingEnabled}
          onChange={onToggleRecording}
        />
      </div>

      {/* --- Individual Camera Toggles --- */}
      <div
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-px bg-gray-700/60 border-t border-gray-700 rounded-b-lg overflow-hidden"
      >
        {cameras.map((camera) => (
          <div
            key={camera.id}
            className="flex items-center justify-between px-4 py-3 bg-gray-800/80 hover:bg-gray-700/40"
          >
            <span className="font-medium text-white truncate mr-2">{camera.name}</span>
            <ToggleSwitch
              enabled={localSchedule[camera.id] || false}
              onChange={() => handleToggle(camera.id)}
            />
          </div>
        ))}
      </div>
    </Card>
  );
}
