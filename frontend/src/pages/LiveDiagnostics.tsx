import React, { useCallback, useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import { Loader2, Radio, Cpu, RefreshCw, Timer } from 'lucide-react';

interface FrontendLatency {
  profile?: string;
  acquireAt?: string | null;
  manifestLoadedAt?: string | null;
  videoPlayingAt?: string | null;
  startupLatencyMs?: number | null;
  liveEdgeDelaySec?: number | null;
  bufferLengthSec?: number | null;
  updatedAt?: string | null;
}

interface StreamLatency {
  profile: string;
  ffmpegStartAt: string | null;
  rtspConnectedAt: string | null;
  playlistCreatedAt: string | null;
  firstSegmentCreatedAt: string | null;
  playlistReadyAt: string | null;
  rtspConnectMs: number | null;
  playlistCreatedMs: number | null;
  firstSegmentMs: number | null;
  backendStartupMs: number | null;
  approxTotalStartupMs: number | null;
  hlsSegmentDurationSec: number | null;
  hlsTargetDurationSec: number | null;
  hlsPlaylistSegmentCount: number;
  hlsListSizeConfigured: number;
  hlsSegmentSecondsConfigured: number;
  frontend: FrontendLatency | null;
}

interface LiveStreamDiagnostic {
  cameraId: string;
  cameraName: string;
  streamId: string;
  profile: 'grid' | 'fullscreen';
  ffmpegPid: number | null;
  refCount: number;
  playlistReady: boolean;
  lastError: string | null;
  startedAt: string | null;
  startupMs: number | null;
  status: string;
  streamLabel: string | null;
  latency: StreamLatency;
}

interface LiveDiagnosticsResponse {
  activeStreamCount: number;
  ffmpegProcessCount: number;
  hlsConfig?: {
    segmentSeconds: number;
    listSize: number;
    flags: string;
  };
  streams: LiveStreamDiagnostic[];
}

function ms(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v} ms`;
}

function sec(v: number | null | undefined, digits = 1): string {
  if (v == null) return '—';
  return `${v.toFixed(digits)} s`;
}

function formatStartedAt(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

const DIAGNOSTICS_TIMEOUT_MS = 5000;

function diagnosticsErrorMessage(err: unknown): string {
  if (err instanceof DOMException && err.name === 'AbortError') {
    return 'Request timed out after 5 seconds. Check that the backend is running on port 10000.';
  }
  if (err instanceof TypeError) {
    return 'Could not reach GET /api/live/diagnostics. Is the backend running?';
  }
  if (err instanceof Error) {
    return err.message;
  }
  return 'Failed to load diagnostics';
}

async function fetchDiagnosticsApi(signal: AbortSignal): Promise<LiveDiagnosticsResponse> {
  const res = await fetch('/api/live/diagnostics', { cache: 'no-store', signal });
  if (!res.ok) {
    throw new Error(`GET /api/live/diagnostics failed (HTTP ${res.status})`);
  }
  return res.json() as Promise<LiveDiagnosticsResponse>;
}

function LatencyRow({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="flex justify-between gap-4 py-1 text-xs">
      <span className="text-gray-400 shrink-0">{label}</span>
      <span className="text-gray-200 text-right font-mono">
        {value}
        {hint && <span className="block text-[10px] text-gray-500 font-sans">{hint}</span>}
      </span>
    </div>
  );
}

function StreamLatencyCard({ row }: { row: LiveStreamDiagnostic }) {
  const lat = row.latency;
  const fe = lat.frontend;

  return (
    <Card className="p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-semibold text-white">{row.cameraName}</div>
          <div className="text-xs font-mono text-gray-500">{row.streamId}</div>
        </div>
        <span
          className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
            row.profile === 'fullscreen'
              ? 'bg-violet-900/60 text-violet-200'
              : 'bg-sky-900/60 text-sky-200'
          }`}
        >
          {row.profile}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-lg bg-gray-900/50 p-3 space-y-0.5">
          <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Backend HLS</p>
          <LatencyRow label="FFmpeg start" value={formatStartedAt(lat.ffmpegStartAt)} />
          <LatencyRow label="RTSP connected" value={formatStartedAt(lat.rtspConnectedAt)} hint={ms(lat.rtspConnectMs)} />
          <LatencyRow label="Playlist created" value={formatStartedAt(lat.playlistCreatedAt)} hint={ms(lat.playlistCreatedMs)} />
          <LatencyRow label="First segment" value={formatStartedAt(lat.firstSegmentCreatedAt)} hint={ms(lat.firstSegmentMs)} />
          <LatencyRow label="Playlist ready" value={formatStartedAt(lat.playlistReadyAt)} hint={ms(lat.backendStartupMs)} />
          <LatencyRow
            label="HLS segment / list"
            value={`${lat.hlsSegmentDurationSec ?? lat.hlsSegmentSecondsConfigured}s · ${lat.hlsPlaylistSegmentCount}/${lat.hlsListSizeConfigured} segs`}
          />
        </div>

        <div className="rounded-lg bg-gray-900/50 p-3 space-y-0.5">
          <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Frontend HLS.js</p>
          {fe ? (
            <>
              <LatencyRow label="Manifest loaded" value={formatStartedAt(fe.manifestLoadedAt)} />
              <LatencyRow label="Video playing" value={formatStartedAt(fe.videoPlayingAt)} />
              <LatencyRow label="Startup latency" value={ms(fe.startupLatencyMs)} hint="acquire → playing" />
              <LatencyRow label="Live edge delay" value={sec(fe.liveEdgeDelaySec)} hint="hls.js liveSyncPosition" />
              <LatencyRow label="Buffer length" value={sec(fe.bufferLengthSec)} hint="buffered − currentTime" />
              <LatencyRow label="Updated" value={formatStartedAt(fe.updatedAt)} />
            </>
          ) : (
            <p className="text-xs text-gray-500 py-2">No browser telemetry yet (open Live View tile)</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-4 pt-1 border-t border-gray-800 text-xs">
        <div>
          <span className="text-gray-500">Approx total startup </span>
          <span className="text-amber-200 font-mono">{ms(lat.approxTotalStartupMs)}</span>
          <span className="text-gray-600"> (ffmpeg → playing)</span>
        </div>
        <div>
          <span className="text-gray-500">Backend only </span>
          <span className="text-sky-200 font-mono">{ms(lat.backendStartupMs)}</span>
        </div>
        {row.lastError && (
          <div className="text-red-300 truncate max-w-full">Error: {row.lastError}</div>
        )}
      </div>
    </Card>
  );
}

export default function LiveDiagnostics(): React.ReactElement {
  const [data, setData] = useState<LiveDiagnosticsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchDiagnostics = useCallback(async (withLoading = false) => {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), DIAGNOSTICS_TIMEOUT_MS);

    if (withLoading) {
      setLoading(true);
      setError(null);
    }

    try {
      const json = await fetchDiagnosticsApi(controller.signal);
      setData(json);
      setError(null);
    } catch (err) {
      setError(diagnosticsErrorMessage(err));
    } finally {
      window.clearTimeout(timeoutId);
      setLoading(false);
    }
  }, []);

  const handleRetry = useCallback(() => {
    void fetchDiagnostics(true);
  }, [fetchDiagnostics]);

  useEffect(() => {
    void fetchDiagnostics(true);
  }, [fetchDiagnostics]);

  useEffect(() => {
    if (!autoRefresh || !data) return;
    const id = setInterval(() => {
      void fetchDiagnostics(false);
    }, 3000);
    return () => clearInterval(id);
  }, [autoRefresh, data, fetchDiagnostics]);

  return (
    <div className="h-full min-h-0 flex flex-col overflow-hidden">
      <PageHeader
        title="Live Diagnostics"
        subtitle="Latency measurement — backend HLS segmenting vs frontend buffer (no optimizations applied)"
        rightContent={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="rounded border-gray-500"
              />
              Auto-refresh (3s)
            </label>
            <button
              type="button"
              onClick={() => void fetchDiagnostics(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-gray-700 text-gray-100 text-sm hover:bg-gray-600"
            >
              <RefreshCw size={14} />
              Refresh
            </button>
          </div>
        }
      />

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
        {loading && !data && !error && (
          <div className="flex items-center justify-center py-16 text-gray-500">
            <Loader2 className="animate-spin mr-2" size={24} />
            Loading diagnostics…
          </div>
        )}

        {error && !data && !loading && (
          <Card className="max-w-lg mx-auto mt-12 p-6 text-center space-y-4">
            <p className="text-red-200 text-sm leading-relaxed">{error}</p>
            <p className="text-gray-500 text-xs">
              Endpoint: <span className="font-mono">GET /api/live/diagnostics</span>
            </p>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md bg-blue-600 text-white text-sm hover:bg-blue-500"
            >
              <RefreshCw size={14} />
              Retry
            </button>
          </Card>
        )}

        {error && data && (
          <div className="rounded-lg border border-amber-700/50 bg-amber-950/40 px-4 py-3 text-amber-200 text-sm flex flex-wrap items-center justify-between gap-3">
            <span>Refresh failed: {error}</span>
            <button
              type="button"
              onClick={handleRetry}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-amber-800 text-white text-xs hover:bg-amber-700"
            >
              <RefreshCw size={12} />
              Retry
            </button>
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-4xl">
              <Card className="flex items-center gap-4">
                <div className="p-3 rounded-full bg-blue-900/50 text-blue-300">
                  <Radio size={22} />
                </div>
                <div>
                  <p className="text-sm text-gray-400">Active live streams</p>
                  <p className="text-2xl font-bold text-white">{data.activeStreamCount}</p>
                </div>
              </Card>
              <Card className="flex items-center gap-4">
                <div className="p-3 rounded-full bg-purple-900/50 text-purple-300">
                  <Cpu size={22} />
                </div>
                <div>
                  <p className="text-sm text-gray-400">FFmpeg processes</p>
                  <p className="text-2xl font-bold text-white">{data.ffmpegProcessCount}</p>
                </div>
              </Card>
              <Card className="flex items-center gap-4">
                <div className="p-3 rounded-full bg-amber-900/50 text-amber-300">
                  <Timer size={22} />
                </div>
                <div>
                  <p className="text-sm text-gray-400">HLS config</p>
                  <p className="text-lg font-bold text-white">
                    {data.hlsConfig
                      ? `${data.hlsConfig.segmentSeconds}s × ${data.hlsConfig.listSize}`
                      : '—'}
                  </p>
                </div>
              </Card>
            </div>

            <Card className="overflow-hidden p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="bg-gray-800/80 text-gray-300 text-xs uppercase tracking-wide">
                    <tr>
                      <th className="px-3 py-2.5 font-semibold">Camera</th>
                      <th className="px-3 py-2.5 font-semibold">Profile</th>
                      <th className="px-3 py-2.5 font-semibold">Backend startup</th>
                      <th className="px-3 py-2.5 font-semibold">1st segment</th>
                      <th className="px-3 py-2.5 font-semibold">Frontend startup</th>
                      <th className="px-3 py-2.5 font-semibold">Live edge</th>
                      <th className="px-3 py-2.5 font-semibold">Buffer</th>
                      <th className="px-3 py-2.5 font-semibold">Total est.</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-800">
                    {data.streams.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-3 py-8 text-center text-gray-500">
                          No live streams in registry
                        </td>
                      </tr>
                    ) : (
                      data.streams.map((row) => (
                        <tr key={row.streamId} className="hover:bg-gray-800/40">
                          <td className="px-3 py-2.5 text-white whitespace-nowrap">
                            <div className="font-medium">{row.cameraName}</div>
                            <div className="text-xs text-gray-500 font-mono">{row.streamId}</div>
                          </td>
                          <td className="px-3 py-2.5">
                            <span
                              className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                                row.profile === 'fullscreen'
                                  ? 'bg-violet-900/60 text-violet-200'
                                  : 'bg-sky-900/60 text-sky-200'
                              }`}
                            >
                              {row.profile}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-300 text-xs">
                            {ms(row.latency.backendStartupMs)}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-300 text-xs">
                            {ms(row.latency.firstSegmentMs)}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-300 text-xs">
                            {ms(row.latency.frontend?.startupLatencyMs)}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-300 text-xs">
                            {sec(row.latency.frontend?.liveEdgeDelaySec)}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-300 text-xs">
                            {sec(row.latency.frontend?.bufferLengthSec)}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-amber-200 text-xs">
                            {ms(row.latency.approxTotalStartupMs)}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </Card>

            {data.streams.length > 0 && (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
                  Per-stream latency detail
                </h3>
                {data.streams.map((row) => (
                  <StreamLatencyCard key={`detail-${row.streamId}`} row={row} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
