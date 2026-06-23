import React, { useCallback, useEffect, useRef, useState } from 'react';
import PageHeader from '../components/PageHeader';
import Card from '../components/Card';
import { Loader2, RefreshCw, Radio, Zap } from 'lucide-react';
import Hls from 'hls.js';
import { mountGo2RtcPlayer } from '../lib/go2rtcPlayer';
import { measureHlsLiveMetrics } from '../lib/liveLatencyTelemetry';
import { livePlaylistUrl } from '../lib/liveStreamRegistry';

interface Go2RtcStatus {
  enabled: boolean;
  running: boolean;
  pilotCamera: string;
  streamSub: string;
  streamMain: string;
  binaryFound: boolean;
  binary: string;
  camera?: { id?: string; name?: string; error?: string };
}

interface RowMetrics {
  startupMs: number | null;
  liveDelaySec: number | null;
  bufferSec: number | null;
  status: string;
  modeLabel: string | null;
}

const EMPTY: RowMetrics = {
  startupMs: null,
  liveDelaySec: null,
  bufferSec: null,
  status: 'idle',
  modeLabel: null,
};

function fmtMs(v: number | null): string {
  return v == null ? '—' : `${v} ms`;
}

function fmtSec(v: number | null): string {
  return v == null ? '—' : `${v.toFixed(1)} s`;
}

export default function LiveRealtimeTest(): React.ReactElement {
  const [status, setStatus] = useState<Go2RtcStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeStream, setActiveStream] = useState<'sub' | 'main'>('sub');
  const [metrics, setMetrics] = useState({
    hls: { ...EMPTY },
    webrtc: { ...EMPTY },
  });

  const hlsVideoRef = useRef<HTMLVideoElement>(null);
  const webrtcRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cameraIdRef = useRef<string | null>(null);

  const streamName = activeStream === 'sub' ? status?.streamSub : status?.streamMain;

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/go2rtc/status', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: Go2RtcStatus = await res.json();
      setStatus(data);
      if (!data.running) {
        const start = await fetch('/api/go2rtc/start', { method: 'POST' });
        const started = await start.json();
        if (started.running) {
          const again = await fetch('/api/go2rtc/status');
          setStatus(await again.json());
        } else if (started.error) {
          setError(started.error + (started.hint ? ` — ${started.hint}` : ''));
        }
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load go2rtc status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    const cameraId = status?.camera?.id;
    if (!cameraId || !status?.running) return;

    cameraIdRef.current = cameraId;
    const ac = new AbortController();
    let webrtcUnmount: (() => void) | null = null;

    setMetrics({ hls: { ...EMPTY, status: 'starting' }, webrtc: { ...EMPTY, status: 'starting' } });

    const run = async () => {
      const stream = streamName ?? status.streamSub;
      const fullscreen = activeStream === 'main';
      const hlsT0 = performance.now();

      // --- HLS V1 (fallback path) ---
      try {
        await fetch(`/api/live/${fullscreen ? `${cameraId}__fullscreen` : cameraId}/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ forceSub: false }),
          signal: ac.signal,
        });
      } catch {
        // continue
      }

      const video = hlsVideoRef.current;
      if (video && Hls.isSupported()) {
        hlsRef.current?.destroy();
        const hls = new Hls({
          lowLatencyMode: true,
          liveSyncDurationCount: 1,
          liveMaxLatencyDurationCount: 3,
          maxBufferLength: 4,
          maxMaxBufferLength: 6,
          backBufferLength: 0,
          maxLiveSyncPlaybackRate: 1.5,
        });
        hlsRef.current = hls;
        const url = livePlaylistUrl(cameraId, fullscreen);
        hls.loadSource(`${url}?_=${Date.now()}`);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          const edge = hls.liveSyncPosition;
          if (edge != null) video.currentTime = edge;
          void video.play();
        });
        const onHlsPlaying = () => {
          const m = measureHlsLiveMetrics(hls, video);
          setMetrics((prev) => ({
            ...prev,
            hls: {
              startupMs: Math.round(performance.now() - hlsT0),
              liveDelaySec: m.liveEdgeDelaySec,
              bufferSec: m.bufferLengthSec,
              status: 'playing',
              modeLabel: 'HLS V1',
            },
          }));
        };
        video.addEventListener('playing', onHlsPlaying, { once: true });
      }

      // --- go2rtc WebRTC (one consumer per stream) ---
      if (webrtcRef.current) {
        try {
          webrtcUnmount = await mountGo2RtcPlayer(webrtcRef.current, {
            stream,
            mode: 'webrtc',
            onFirstFrame: (ms) =>
              setMetrics((p) => ({
                ...p,
                webrtc: { ...p.webrtc, startupMs: ms, status: 'playing' },
              })),
            onModeLabel: (label) =>
              setMetrics((p) => ({ ...p, webrtc: { ...p.webrtc, modeLabel: label } })),
            onError: (msg) =>
              setMetrics((p) => ({ ...p, webrtc: { ...p.webrtc, status: 'error', modeLabel: msg } })),
          });
        } catch (err) {
          setMetrics((p) => ({
            ...p,
            webrtc: { ...p.webrtc, status: 'error', modeLabel: String(err) },
          }));
        }
      }

      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(() => {
        const v = hlsVideoRef.current;
        const h = hlsRef.current;
        if (v && h) {
          const m = measureHlsLiveMetrics(h, v);
          setMetrics((p) => ({
            ...p,
            hls: {
              ...p.hls,
              liveDelaySec: m.liveEdgeDelaySec,
              bufferSec: m.bufferLengthSec,
            },
          }));
        }
      }, 3000);
    };

    void run();

    return () => {
      ac.abort();
      webrtcUnmount?.();
      hlsRef.current?.destroy();
      hlsRef.current = null;
      const video = hlsVideoRef.current;
      if (video) {
        video.pause();
        video.removeAttribute('src');
        video.load();
      }
      const id = cameraIdRef.current;
      if (id) {
        void fetch(`/api/live/${id}/stop`, { method: 'POST', keepalive: true }).catch(() => {});
        if (activeStream === 'main') {
          void fetch(`/api/live/${id}__fullscreen/stop`, { method: 'POST', keepalive: true }).catch(() => {});
        }
      }
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [status?.camera?.id, status?.running, streamName, activeStream, status?.streamSub]);

  useEffect(() => {
    return () => {
      const id = cameraIdRef.current;
      if (id) {
        void fetch(`/api/live/${id}/stop`, { method: 'POST', keepalive: true }).catch(() => {});
      }
    };
  }, []);

  const rows = [
    { key: 'hls', label: 'HLS V1 (fallback)', data: metrics.hls },
    { key: 'webrtc', label: 'go2rtc WebRTC', data: metrics.webrtc },
  ] as const;

  return (
    <div className="h-full min-h-0 flex flex-col overflow-hidden">
      <PageHeader
        title="Realtime Live Test (Phase 1)"
        subtitle="Latency comparison — sub 102 by default. Main 101 opens extra go2rtc consumers."
        rightContent={
          <button
            type="button"
            onClick={() => { setLoading(true); void fetchStatus(); }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-gray-700 text-white text-sm"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading && (
          <div className="flex items-center gap-2 text-gray-500 py-8 justify-center">
            <Loader2 className="animate-spin" size={20} /> Loading…
          </div>
        )}

        {error && (
          <Card className="p-4 border border-red-800 bg-red-950/40 text-red-200 text-sm">{error}</Card>
        )}

        {status && (
          <>
            <div className="flex flex-wrap gap-3 items-center">
              <span className={`px-2 py-1 rounded text-xs ${status.running ? 'bg-green-900 text-green-200' : 'bg-red-900 text-red-200'}`}>
                go2rtc {status.running ? 'running' : 'stopped'}
              </span>
              <span className="text-sm text-gray-400">Pilot: {status.pilotCamera}</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setActiveStream('sub')}
                  className={`px-3 py-1 rounded text-sm ${activeStream === 'sub' ? 'bg-sky-700 text-white' : 'bg-gray-800 text-gray-300'}`}
                >
                  Sub 102 — {status.streamSub}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveStream('main')}
                  className={`px-3 py-1 rounded text-sm ${activeStream === 'main' ? 'bg-violet-700 text-white' : 'bg-gray-800 text-gray-300'}`}
                  title="Opens main 101 — only use for testing"
                >
                  Main 101 — {status.streamMain}
                </button>
              </div>
            </div>

            {activeStream === 'main' && (
              <Card className="p-3 border border-amber-700/50 bg-amber-950/30 text-amber-100 text-sm">
                Main stream is active — this page holds a go2rtc main consumer. Switch back to Sub or leave
                this page when done testing.
              </Card>
            )}

            <Card className="overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead className="bg-gray-800/80 text-gray-300 text-xs uppercase">
                  <tr>
                    <th className="px-3 py-2 text-left">Engine</th>
                    <th className="px-3 py-2 text-left">Startup</th>
                    <th className="px-3 py-2 text-left">Live delay</th>
                    <th className="px-3 py-2 text-left">Buffer</th>
                    <th className="px-3 py-2 text-left">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <td className="px-3 py-2 text-white">{r.label}</td>
                      <td className="px-3 py-2 font-mono text-xs">{fmtMs(r.data.startupMs)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{fmtSec(r.data.liveDelaySec)}</td>
                      <td className="px-3 py-2 font-mono text-xs">{fmtSec(r.data.bufferSec)}</td>
                      <td className="px-3 py-2 text-xs text-gray-300">{r.data.modeLabel ?? r.data.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <Card className="p-2">
                <div className="flex items-center gap-2 px-2 py-1 text-xs text-gray-400">
                  <Radio size={14} /> HLS V1
                </div>
                <div className="aspect-video bg-black rounded overflow-hidden">
                  <video ref={hlsVideoRef} playsInline muted className="w-full h-full object-contain" />
                </div>
              </Card>
              <Card className="p-2">
                <div className="flex items-center gap-2 px-2 py-1 text-xs text-gray-400">
                  <Zap size={14} /> go2rtc WebRTC
                </div>
                <div ref={webrtcRef} className="aspect-video bg-black rounded overflow-hidden" />
              </Card>
            </div>

            {!status.binaryFound && (
              <Card className="p-4 text-amber-200 text-sm bg-amber-950/30 border border-amber-800">
                Place go2rtc binary at <code className="font-mono">{status.binary}</code> or set GO2RTC_BIN in .env.
                Download: github.com/AlexxIT/go2rtc/releases
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
