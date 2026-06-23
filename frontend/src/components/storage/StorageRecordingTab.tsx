import React, { useMemo } from 'react';
import { AlertTriangle, Circle } from 'lucide-react';
import RecordingLocationSummary from './RecordingLocationSummary';
import RecordingSchedule from '../RecordingSchedule';
import RecordingHealthMonitor from '../RecordingHealthMonitor';
import { StorageDashboardData } from '../../hooks/useStorageDashboard';

interface Camera {
  id: string;
  name: string;
  site?: string;
  building?: string;
  floor?: string;
}

function inferFloorFromName(name: string): string | undefined {
  const match = (name || '').trim().match(/^(\d+F|GF|B\d+F)/i);
  return match ? match[1].toUpperCase() : undefined;
}

function enrichCamerasWithLocation(
  cameras: Camera[],
  storageCameras: StorageDashboardData['cameras'] | undefined,
): Camera[] {
  const locById = new Map((storageCameras ?? []).map((c) => [c.camera_id, c]));
  return cameras.map((cam) => {
    const loc = locById.get(cam.id);
    const floor = cam.floor?.trim() || loc?.floor?.trim() || inferFloorFromName(cam.name) || '';
    const building = cam.building?.trim() || loc?.building?.trim() || '';
    const site = cam.site?.trim() || loc?.site?.trim() || '';
    return { ...cam, site, building, floor };
  });
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
  const scheduleCameras = useMemo(
    () => enrichCamerasWithLocation(cameras, data?.cameras),
    [cameras, data?.cameras],
  );

  return (
    <div className="space-y-4 w-full">
      <div
        className={`flex items-center justify-between gap-3 rounded-lg border px-4 py-3 ${
          isRecordingEnabled
            ? 'bg-emerald-500/10 border-emerald-500/30'
            : 'bg-gray-800/80 border-gray-600'
        }`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Circle
            size={10}
            className={`shrink-0 ${
              isRecordingEnabled ? 'fill-emerald-400 text-emerald-400' : 'fill-gray-500 text-gray-500'
            }`}
          />
          <span
            className={`text-sm font-semibold ${
              isRecordingEnabled ? 'text-emerald-300' : 'text-gray-400'
            }`}
          >
            {isRecordingEnabled ? 'Recording Active' : 'Recording Disabled'}
          </span>
          <span className="text-xs text-gray-500 hidden md:inline">
            {isRecordingEnabled
              ? '— scheduled cameras are writing to disk'
              : '— enable below to start recording'}
          </span>
        </div>
      </div>

      {data?.recordingByLocation && data.recordingByLocation.length > 0 && (
        <RecordingLocationSummary sites={data.recordingByLocation} />
      )}

      <RecordingHealthMonitor locationCameras={data?.cameras} />

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

      <RecordingSchedule
        cameras={scheduleCameras}
        schedule={schedule}
        onSave={onScheduleChange}
        isRecordingEnabled={isRecordingEnabled}
        onToggleRecording={onToggleMasterRecording}
      />
    </div>
  );
}
