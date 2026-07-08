import React, { useState, useEffect, useMemo } from 'react';
import toast from 'react-hot-toast';
import { HardDrive, Video, Settings, RefreshCw, Loader2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import StorageOverviewTab from '../components/storage/StorageOverviewTab';
import StorageRecordingTab from '../components/storage/StorageRecordingTab';
import StorageSettingsTab from '../components/storage/StorageSettingsTab';
import { useStorageDashboard } from '../hooks/useStorageDashboard';
import { apiFetch } from '../lib/api';
import Card from '../components/Card';
import {
  useUrlHydration,
  useUrlSync,
  initialEnumParam,
} from '../hooks/useUrlSearchState';

type StorageTab = 'storage' | 'recording' | 'settings';

const TABS: { id: StorageTab; label: string; icon: React.ReactNode }[] = [
  { id: 'storage', label: 'Storage', icon: <HardDrive size={16} /> },
  { id: 'recording', label: 'Recording', icon: <Video size={16} /> },
  { id: 'settings', label: 'Settings', icon: <Settings size={16} /> },
];

interface StorageProps {
  schedule: Record<string, boolean>;
  isRecordingEnabled: boolean;
  onScheduleChange: (newSchedule: Record<string, boolean>) => void;
  onToggleMasterRecording: (enabled: boolean) => void;
}

export default function Storage({
  schedule,
  isRecordingEnabled,
  onScheduleChange,
  onToggleMasterRecording,
}: StorageProps): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [activeTab, setActiveTab] = useState<StorageTab>(() =>
    initialEnumParam(initialParams, 'tab', ['storage', 'recording', 'settings'] as const, 'storage'),
  );
  const [allCameras, setAllCameras] = useState<
    { id: string; name: string; site?: string; building?: string; floor?: string }[]
  >([]);
  const { data, loading, loadingFull, error, refresh, runRetention, runningRetention } = useStorageDashboard();

  useEffect(() => {
    const fetchCameras = async () => {
      try {
        const response = await apiFetch('/api/cameras');
        const data = await response.json();
        setAllCameras(data);
      } catch {
        toast.error('Could not load camera list for schedule.');
      }
    };
    fetchCameras();
  }, []);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({ tab: activeTab === 'storage' ? null : activeTab }),
    [activeTab],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Storage & Recording"
        subtitle="Disk usage, retention settings, and recording status by location"
        rightContent={
          loadingFull ? (
            <span className="text-xs text-gray-500 flex items-center gap-1">
              <Loader2 size={12} className="animate-spin" /> Scanning recordings…
            </span>
          ) : undefined
        }
      />

      <div className="flex flex-col flex-1 min-h-0 px-4 pb-4">
        {/* Tab bar — full width */}
        <div className="flex items-center gap-8 mb-4 flex-shrink-0 w-full">
          <div className="flex flex-1 max-w-2xl gap-1.5 bg-gray-800/80 border border-gray-700 rounded-lg p-1.5">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex-1 inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-md text-sm font-medium transition-colors whitespace-nowrap min-w-[9rem] ${
                  activeTab === tab.id
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'text-gray-400 hover:text-white hover:bg-gray-700/60'
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => refresh()}
            disabled={loading}
            className="ml-auto flex flex-shrink-0 items-center gap-1.5 text-xs px-4 py-2 rounded-md bg-gray-700 hover:bg-gray-600 text-gray-300 disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>

        {/* Tab content — full width */}
        <div className="flex-1 min-h-0 overflow-y-auto w-full">
          {loading && !data && (
            <Card className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 className="animate-spin mr-2" size={20} />
              Loading…
            </Card>
          )}

          {error && !data && (
            <Card className="py-8 text-center text-red-400">{error}</Card>
          )}

          {activeTab === 'storage' && data && <StorageOverviewTab data={data} />}

          {activeTab === 'recording' && (
            <StorageRecordingTab
              cameras={allCameras}
              schedule={schedule}
              isRecordingEnabled={isRecordingEnabled}
              onScheduleChange={onScheduleChange}
              onToggleMasterRecording={onToggleMasterRecording}
              data={data}
            />
          )}

          {activeTab === 'settings' && data && (
            <StorageSettingsTab
              data={data}
              onRunRetention={runRetention}
              runningRetention={runningRetention}
              onSettingsSaved={() => void refresh()}
            />
          )}
        </div>
      </div>
    </div>
  );
}
