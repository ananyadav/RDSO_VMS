import React, { useState, useEffect, useMemo } from 'react';
import PageHeader from '../components/PageHeader';
import { Server, HardDrive, Cpu, MemoryStick, Thermometer, ShieldCheck, Loader2 } from 'lucide-react';
import Card from '../components/Card';
import { apiFetch } from '../lib/api';
import {
  useUrlHydration,
  useUrlSync,
  paramFlag,
  flagParam,
} from '../hooks/useUrlSearchState';

// --- The Reusable Toggle Switch Component ---
interface ToggleSwitchProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  label: string;
}
const ToggleSwitch = ({ enabled, onChange, label }: ToggleSwitchProps) => (
  <div className="flex items-center justify-between py-3">
    <span className="font-medium text-white">{label}</span>
    <button
      type="button"
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800 ${
        enabled ? 'bg-blue-600' : 'bg-gray-600'
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
          enabled ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  </div>
);

// --- System Info Card Component ---
interface InfoCardProps {
  icon: React.ReactNode;
  title: string;
  value: string;
  colorClass: string;
}
const InfoCard = ({ icon, title, value, colorClass }: InfoCardProps) => (
  <Card className="flex items-center">
    <div className={`p-3 rounded-full mr-4 ${colorClass}`}>
      {icon}
    </div>
    <div>
      <p className="text-sm text-gray-400">{title}</p>
      <p className="text-xl font-bold text-white">{value}</p>
    </div>
  </Card>
);

interface SystemStats {
  uptime: string;
  cpu_percent: number | null;
  memory_percent: number | null;
  disk_percent: number | null;
  video_decode?: { description?: string };
}

export default function SystemStatus(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [autoReboot, setAutoReboot] = useState(() =>
    paramFlag(initialParams.current?.get('autoReboot') ?? null, true),
  );
  const [smartDetection, setSmartDetection] = useState(() =>
    paramFlag(initialParams.current?.get('smartDetect') ?? null, true),
  );
  const [firmwareUpdates, setFirmwareUpdates] = useState(() =>
    paramFlag(initialParams.current?.get('firmware') ?? null, false),
  );
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({
      autoReboot: flagParam(autoReboot),
      smartDetect: flagParam(smartDetection),
      firmware: flagParam(firmwareUpdates),
    }),
    [autoReboot, smartDetection, firmwareUpdates],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await apiFetch('/api/status');
        if (res.ok) {
          setStats(await res.json());
          setFetchError(null);
        } else {
          setFetchError(`Status API returned ${res.status}`);
        }
      } catch {
        setFetchError('Could not reach status API');
      } finally {
        setLoading(false);
      }
    };
    void fetchStatus();
    const interval = setInterval(() => void fetchStatus(), 30000);
    return () => clearInterval(interval);
  }, []);

  const fmt = (val: number | null | undefined, suffix: string) =>
    val != null ? `${val}${suffix}` : '—';

  return (
    <div className="flex flex-col h-full space-y-6">
      <PageHeader
        title="System Status"
        subtitle="Monitor system health and performance"
      />

      {fetchError && (
        <p className="text-sm text-amber-400 px-1">{fetchError}</p>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="animate-spin text-gray-400" size={32} />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          <InfoCard icon={<Server size={24} />} title="Uptime" value={stats?.uptime ?? '—'} colorClass="bg-green-500/20 text-green-300" />
          <InfoCard icon={<Cpu size={24} />} title="CPU Load" value={fmt(stats?.cpu_percent, '%')} colorClass="bg-blue-500/20 text-blue-300" />
          <InfoCard icon={<MemoryStick size={24} />} title="Memory Usage" value={fmt(stats?.memory_percent, '%')} colorClass="bg-yellow-500/20 text-yellow-300" />
          <InfoCard icon={<HardDrive size={24} />} title="Storage Used" value={fmt(stats?.disk_percent, '%')} colorClass="bg-purple-500/20 text-purple-300" />
          <InfoCard icon={<Thermometer size={24} />} title="Temperature" value="N/A" colorClass="bg-orange-500/20 text-orange-300" />
          <InfoCard icon={<ShieldCheck size={24} />} title="Video Decode" value={stats?.video_decode?.description ?? '—'} colorClass="bg-gray-500/20 text-gray-300" />
        </div>
      )}

      <Card>
        <h3 className="text-lg font-bold text-white mb-2">System Automation</h3>
        <div className="divide-y divide-gray-700">
          <ToggleSwitch
            label="Enable Weekly Auto Reboot"
            enabled={autoReboot}
            onChange={setAutoReboot}
          />
          <ToggleSwitch
            label="Enable S.M.A.R.T. Disk Detection"
            enabled={smartDetection}
            onChange={setSmartDetection}
          />
          <ToggleSwitch
            label="Enable Automatic Firmware Updates"
            enabled={firmwareUpdates}
            onChange={setFirmwareUpdates}
          />
        </div>
      </Card>
    </div>
  );
}
