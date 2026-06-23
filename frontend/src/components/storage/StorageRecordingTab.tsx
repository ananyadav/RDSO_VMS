import React from 'react';
import { Video, Clock, AlertTriangle, ShieldCheck } from 'lucide-react';
import Card from '../Card';
import RecordingLocationSummary from './RecordingLocationSummary';
import RecordingSchedule from '../RecordingSchedule';
import { StorageDashboardData } from '../../hooks/useStorageDashboard';

interface Camera {
  id: string;
  name: string;
}

interface StorageRecordingTabProps {
  cameras: Camera[];
  schedule: Record<string, boolean>;
  isRecordingEnabled: boolean;
  onScheduleChange: (s: Record<string, boolean>) => void;
  onToggleMasterRecording: (enabled: boolean) => void;
  data: StorageDashboardData | null;
}

export default function StorageRecordingTab({
  cameras,
  schedule,
  isRecordingEnabled,
  onScheduleChange,
  onToggleMasterRecording,
  data,
}: StorageRecordingTabProps) {
  const recordingInfo = data?.recording;
  const isSubstream = recordingInfo?.substream_warning ?? false;

  return (
    <div className="space-y-4 w-full">
      {data?.recordingByLocation && data.recordingByLocation.length > 0 && (
        <RecordingLocationSummary sites={data.recordingByLocation} />
      )}

      {isSubstream && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          <AlertTriangle size={18} className="flex-shrink-0 mt-0.5 text-amber-400" />
          <div>
            <p className="font-medium text-amber-100">Recording is using substream (channel 102)</p>
            <p className="mt-1 text-amber-200/90">
              Saved footage will be low resolution (typically 640×360). Set{' '}
              <code className="text-amber-100">RECORDING_STREAM=main</code> in server .env and restart
              the backend for evidence-quality recordings.
            </p>
          </div>
        </div>
      )}

      <Card>
        <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-700">
          <Video size={18} className="text-gray-400" />
          <h3 className="text-sm font-semibold text-white">Recording Configuration</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 text-sm">
          <div className="bg-gray-700/40 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Stream Quality</p>
            <p className="text-white font-medium flex items-center gap-1.5">
              {isSubstream ? (
                <AlertTriangle size={14} className="text-amber-400" />
              ) : (
                <ShieldCheck size={14} className="text-green-400" />
              )}
              {recordingInfo?.quality_label ?? 'Main Stream / Evidence Quality'}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              Channel {recordingInfo?.channel ?? '101'} · {recordingInfo?.codec_mode ?? 'copy'} (no transcode)
            </p>
          </div>
          <div className="bg-gray-700/40 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Recording Mode</p>
            <p className="text-white font-medium">Continuous</p>
            <p className="text-xs text-gray-500 mt-1">24/7 recording when enabled</p>
          </div>
          <div className="bg-gray-700/40 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Retention Policy</p>
            <p className="text-white font-medium flex items-center gap-1">
              <Clock size={14} className="text-gray-400" />
              {data?.retention?.label ?? '15 days (default)'}
            </p>
            <p className="text-xs text-gray-500 mt-1">Configure in Settings tab / server .env</p>
          </div>
          <div className="bg-gray-700/40 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Cameras</p>
            <p className="text-white font-medium">{cameras.length}</p>
            <p className="text-xs text-gray-500 mt-1">Registered in system</p>
          </div>
          <div className="bg-gray-700/40 rounded-lg p-4">
            <p className="text-xs text-gray-400 uppercase tracking-wide mb-1">Master Recording</p>
            <p className={`font-medium ${isRecordingEnabled ? 'text-green-400' : 'text-gray-400'}`}>
              {isRecordingEnabled ? 'Enabled' : 'Disabled'}
            </p>
            <p className="text-xs text-gray-500 mt-1">Toggle below to change</p>
          </div>
        </div>
      </Card>

      <RecordingSchedule
        cameras={cameras}
        schedule={schedule}
        onSave={onScheduleChange}
        isRecordingEnabled={isRecordingEnabled}
        onToggleRecording={onToggleMasterRecording}
      />
    </div>
  );
}
