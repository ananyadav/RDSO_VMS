import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import { apiFetch } from '../lib/api';
import { Loader2, RefreshCw, Radio, Zap, Play, Square, MapPin, AlertTriangle } from 'lucide-react';

interface StreamRow {
  cameraId: string;
  cameraName: string;
  site?: string;
  building?: string;
  floor?: string;
  camera_group?: string;
  subStream: string;
  mainStream: string;
  subOnline: boolean;
  mainOnline: boolean;
  subConsumers: number;
  mainConsumers: number;
  uiSubConsumers: number;
  uiMainConsumers: number;
  subStaleConsumers: number;
  mainStaleConsumers: number;
  subOrphaned: boolean;
  mainOrphaned: boolean;
  issueCategory?: string;
  issueLabel?: string;
  issueMessage?: string;
}

interface LocationFloor {
  floor: string;
  cameraCount: number;
}

interface LocationBuilding {
  building: string;
  floors: LocationFloor[];
}

interface LocationSite {
  site: string;
  buildings: LocationBuilding[];
}

interface IssueSummary {
  counts: Record<string, number>;
  byCategory: Record<string, { cameraId: string; cameraName: string; message: string; site?: string; building?: string; floor?: string }[]>;
  totalWithIssues: number;
}

interface Diagnostics {
  enabled: boolean;
  running: boolean;
  liveProvider: string;
  apiUrl: string;
  binaryFound: boolean;
  pid: number | null;
  streamCount: number;
  cameraCount: number;
  configuredStreamCount: number;
  camerasOnline: number;
  camerasOffline: number;
  activeConsumers: number;
  uiTrackedConsumers: number;
  configErrors: string[];
  missingInGo2rtc?: string[];
  staleInGo2rtc?: string[];
  streams: StreamRow[];
  errors?: string[];
  locations?: LocationSite[];
  issueSummary?: IssueSummary;
  issueLabels?: Record<string, string>;
}

const ISSUE_STYLES: Record<string, string> = {
  wrong_password: 'bg-red-900/50 text-red-200 border-red-700/50',
  timeout: 'bg-amber-900/40 text-amber-200 border-amber-700/50',
  codec: 'bg-purple-900/40 text-purple-200 border-purple-700/50',
  offline: 'bg-gray-800 text-gray-300 border-gray-600',
  missing_url: 'bg-orange-900/40 text-orange-200 border-orange-700/50',
  other: 'bg-yellow-900/30 text-yellow-200 border-yellow-700/40',
  online: 'bg-green-900/40 text-green-200 border-green-700/50',
};

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${
        ok ? 'bg-green-900/60 text-green-200' : 'bg-red-900/60 text-red-200'
      }`}
    >
      {label}
    </span>
  );
}

function IssueBadge({ category, label }: { category?: string; label?: string }) {
  const cat = category || 'offline';
  const style = ISSUE_STYLES[cat] || ISSUE_STYLES.other;
  return (
    <span className={`inline-flex px-2 py-0.5 rounded border text-xs font-medium ${style}`}>
      {label || cat}
    </span>
  );
}

function ConsumerCell({
  api,
  ui,
  stale,
  orphaned,
}: {
  api: number;
  ui: number;
  stale: number;
  orphaned: boolean;
}) {
  return (
    <span className={orphaned ? 'text-amber-300' : 'text-gray-300'}>
      {api}
      <span className="text-gray-500"> (ui {ui})</span>
      {stale > 0 && (
        <span className="text-amber-400" title="go2rtc API consumers not tracked by this UI session">
          {' '}
          +{stale} stale
        </span>
      )}
    </span>
  );
}

const selectClass =
  'bg-gray-800 text-gray-200 border border-gray-600 rounded px-2 py-1.5 text-sm min-w-[10rem]';

export default function Go2RtcDiagnostics(): React.ReactElement {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [selectedSite, setSelectedSite] = useState('');
  const [selectedBuilding, setSelectedBuilding] = useState('');
  const [selectedFloor, setSelectedFloor] = useState('');

  const fetchDiagnostics = useCallback(async (initial = false) => {
    if (initial) setLoading(true);
    else setRefreshing(true);
    try {
      const res = await apiFetch('/api/go2rtc/diagnostics', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load diagnostics');
    } finally {
      if (initial) setLoading(false);
      else setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchDiagnostics(true);
    const id = window.setInterval(() => void fetchDiagnostics(false), 3000);
    return () => window.clearInterval(id);
  }, [fetchDiagnostics]);

  const locations = data?.locations ?? [];
  const buildings = useMemo(
    () => locations.find((s) => s.site === selectedSite)?.buildings ?? [],
    [locations, selectedSite],
  );
  const floors = useMemo(
    () => buildings.find((b) => b.building === selectedBuilding)?.floors ?? [],
    [buildings, selectedBuilding],
  );

  const filteredStreams = useMemo(() => {
    if (!data?.streams) return [];
    return data.streams.filter((row) => {
      if (selectedSite && (row.site || '') !== selectedSite) return false;
      if (selectedBuilding && (row.building || '') !== selectedBuilding) return false;
      if (selectedFloor && (row.floor || '') !== selectedFloor) return false;
      return true;
    });
  }, [data?.streams, selectedSite, selectedBuilding, selectedFloor]);

  const issueSummary = data?.issueSummary;
  const issueLabels = data?.issueLabels ?? {};

  const runAction = async (path: string, label: string) => {
    setActionMsg(null);
    try {
      const res = await apiFetch(path, { method: 'POST' });
      const body = await res.json();
      if (!res.ok || body.ok === false) {
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      setActionMsg(`${label} OK`);
      await fetchDiagnostics(false);
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : `${label} failed`);
    }
  };

  const orphanedMain = filteredStreams.filter((r) => r.mainOrphaned).length;

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <PageHeader
        title="go2rtc Diagnostics"
        subtitle={
          lastUpdated
            ? `Live engine status · updated ${lastUpdated.toLocaleTimeString()}`
            : 'Live engine status, streams, and active consumers'
        }
        rightContent={
          <button
            type="button"
            onClick={() => void fetchDiagnostics(false)}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 rounded bg-gray-700 text-gray-100 hover:bg-gray-600 disabled:opacity-60"
          >
            <RefreshCw size={16} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        }
      />

      {loading && !data && (
        <div className="flex justify-center py-12">
          <Loader2 className="animate-spin text-gray-500" size={40} />
        </div>
      )}

      {error && (
        <Card>
          <p className="text-red-400">{error}</p>
        </Card>
      )}

      {data && (
        <>
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <MapPin size={16} className="text-sky-400" />
              <label className="sr-only">Site</label>
              <select
                value={selectedSite}
                onChange={(e) => {
                  setSelectedSite(e.target.value);
                  setSelectedBuilding('');
                  setSelectedFloor('');
                }}
                className={selectClass}
              >
                <option value="">All sites</option>
                {locations.map((s) => (
                  <option key={s.site} value={s.site}>{s.site}</option>
                ))}
              </select>
              {selectedSite && (
                <>
                  <label className="sr-only">Building</label>
                  <select
                    value={selectedBuilding}
                    onChange={(e) => {
                      setSelectedBuilding(e.target.value);
                      setSelectedFloor('');
                    }}
                    className={selectClass}
                  >
                    <option value="">All buildings</option>
                    {buildings.map((b) => (
                      <option key={b.building} value={b.building}>{b.building}</option>
                    ))}
                  </select>
                </>
              )}
              {selectedBuilding && floors.length > 0 && (
                <>
                  <label className="sr-only">Floor</label>
                  <select
                    value={selectedFloor}
                    onChange={(e) => setSelectedFloor(e.target.value)}
                    className={selectClass}
                  >
                    <option value="">All floors</option>
                    {floors.map((f) => (
                      <option key={f.floor} value={f.floor}>{f.floor}</option>
                    ))}
                  </select>
                </>
              )}
              <span className="text-xs text-gray-500">
                Showing {filteredStreams.length} of {data.streams.length} cameras
              </span>
            </div>
          </Card>

          {issueSummary && issueSummary.totalWithIssues > 0 && (
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <AlertTriangle size={18} className="text-amber-400" />
                <h3 className="font-semibold text-white">Stream issues by type</h3>
                <span className="text-xs text-gray-500">{issueSummary.totalWithIssues} camera(s) with issues</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {Object.entries(issueSummary.counts)
                  .filter(([, n]) => n > 0)
                  .map(([cat, count]) => (
                    <div
                      key={cat}
                      className={`rounded-lg border p-3 ${ISSUE_STYLES[cat] || ISSUE_STYLES.other}`}
                    >
                      <p className="font-semibold text-sm">{issueLabels[cat] || cat}</p>
                      <p className="text-2xl font-bold mt-1">{count}</p>
                      <ul className="mt-2 text-xs space-y-0.5 max-h-24 overflow-y-auto opacity-90">
                        {(issueSummary.byCategory[cat] || [])
                          .filter((c) => {
                            if (selectedSite && (c.site || '') !== selectedSite) return false;
                            if (selectedBuilding && (c.building || '') !== selectedBuilding) return false;
                            if (selectedFloor && (c.floor || '') !== selectedFloor) return false;
                            return true;
                          })
                          .slice(0, 8)
                          .map((c) => (
                            <li key={c.cameraId} title={c.message}>
                              {c.cameraName}
                            </li>
                          ))}
                      </ul>
                    </div>
                  ))}
              </div>
            </Card>
          )}

          {orphanedMain > 0 && (
            <Card className="border border-amber-700/50 bg-amber-950/30">
              <p className="text-amber-200 text-sm">
                {orphanedMain} camera(s) have orphaned main-stream consumers (go2rtc API &gt; UI tracked).
                Close fullscreen / leave Live View, or reload go2rtc if counts stay high.
              </p>
            </Card>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <Card>
              <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                <Radio size={16} />
                Engine
              </div>
              <div className="space-y-2">
                <StatusPill ok={data.running} label={data.running ? 'go2rtc running' : 'go2rtc stopped'} />
                <p className="text-sm text-gray-300">Provider: {data.liveProvider}</p>
                <p className="text-xs text-gray-500">PID: {data.pid ?? '—'}</p>
                <p className="text-xs text-gray-500 break-all">{data.apiUrl}</p>
              </div>
            </Card>

            <Card>
              <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                <Zap size={16} />
                Streams
              </div>
              <p className="text-2xl font-bold text-white">{data.configuredStreamCount}</p>
              <p className="text-sm text-gray-400">{data.cameraCount} cameras configured</p>
              <p className="text-xs text-gray-500 mt-1">
                Online: {data.camerasOnline} · Offline: {data.camerasOffline}
              </p>
            </Card>

            <Card>
              <div className="text-gray-400 text-sm mb-2">Consumers</div>
              <p className="text-2xl font-bold text-white">{data.activeConsumers}</p>
              <p className="text-sm text-gray-400">go2rtc API (real WebRTC/MSE)</p>
              <p className="text-xs text-gray-500 mt-1">UI tracked: {data.uiTrackedConsumers}</p>
            </Card>

            <Card>
              <div className="text-gray-400 text-sm mb-2">Actions</div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void runAction('/api/go2rtc/start', 'Start')}
                  className="flex items-center gap-1 px-3 py-1.5 rounded bg-emerald-800 text-emerald-100 hover:bg-emerald-700 text-sm"
                >
                  <Play size={14} />
                  Start
                </button>
                <button
                  type="button"
                  onClick={() => void runAction('/api/go2rtc/reload', 'Reload')}
                  className="flex items-center gap-1 px-3 py-1.5 rounded bg-blue-800 text-blue-100 hover:bg-blue-700 text-sm"
                >
                  <RefreshCw size={14} />
                  Reload config
                </button>
                <button
                  type="button"
                  onClick={() => void runAction('/api/go2rtc/stop', 'Stop')}
                  className="flex items-center gap-1 px-3 py-1.5 rounded bg-red-900 text-red-100 hover:bg-red-800 text-sm"
                >
                  <Square size={14} />
                  Stop
                </button>
              </div>
              {actionMsg && <p className="text-xs text-gray-400 mt-2">{actionMsg}</p>}
              {!data.binaryFound && (
                <p className="text-xs text-amber-400 mt-2">go2rtc binary not found on server</p>
              )}
            </Card>
          </div>

          {(data.configErrors?.length ?? 0) > 0 && (
            <Card>
              <h3 className="font-semibold text-amber-300 mb-2">Config warnings</h3>
              <ul className="text-sm text-amber-100/80 list-disc pl-5 space-y-1">
                {data.configErrors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </Card>
          )}

          {((data.missingInGo2rtc?.length ?? 0) > 0) && (
            <Card>
              <h3 className="font-semibold text-red-300 mb-2">Streams missing in go2rtc</h3>
              <p className="text-sm text-red-100/80 mb-2">
                Live View requests these names but go2rtc does not have them. Click Reload config.
              </p>
              <ul className="text-sm text-red-100/80 list-disc pl-5 space-y-1">
                {data.missingInGo2rtc!.map((s) => (
                  <li key={s} className="font-mono text-xs">{s}</li>
                ))}
              </ul>
            </Card>
          )}

          <Card>
            <h3 className="font-semibold text-white mb-1">Cameras & streams</h3>
            <p className="text-xs text-gray-500 mb-3">
              API = real go2rtc consumers · UI = this browser session · Issue = classified stream error
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="text-gray-400 border-b border-gray-700">
                    <th className="py-2 pr-4">Camera</th>
                    <th className="py-2 pr-4">Location</th>
                    <th className="py-2 pr-4">Issue</th>
                    <th className="py-2 pr-4">Sub 102</th>
                    <th className="py-2 pr-4">Main 101</th>
                    <th className="py-2 pr-4">Sub consumers</th>
                    <th className="py-2">Main consumers</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredStreams.map((row) => (
                    <tr key={row.cameraId} className="border-b border-gray-800">
                      <td className="py-2 pr-4 text-gray-200">{row.cameraName}</td>
                      <td className="py-2 pr-4 text-xs text-gray-500">
                        {[row.site, row.building, row.floor].filter(Boolean).join(' → ') || '—'}
                      </td>
                      <td className="py-2 pr-4" title={row.issueMessage || ''}>
                        <IssueBadge category={row.issueCategory} label={row.issueLabel} />
                      </td>
                      <td className="py-2 pr-4">
                        <StatusPill ok={row.subOnline} label={row.subOnline ? 'online' : 'idle'} />
                      </td>
                      <td className="py-2 pr-4">
                        <StatusPill ok={row.mainOnline} label={row.mainOnline ? 'online' : 'idle'} />
                      </td>
                      <td className="py-2 pr-4">
                        <ConsumerCell
                          api={row.subConsumers}
                          ui={row.uiSubConsumers}
                          stale={row.subStaleConsumers}
                          orphaned={row.subOrphaned}
                        />
                      </td>
                      <td className="py-2">
                        <ConsumerCell
                          api={row.mainConsumers}
                          ui={row.uiMainConsumers}
                          stale={row.mainStaleConsumers}
                          orphaned={row.mainOrphaned}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
