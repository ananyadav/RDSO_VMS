import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
  X,
} from 'lucide-react';
import { usePlaybackHLS } from '../hooks/usePlaybackHLS';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  ensureAppTimezone,
  formatPlaybackClock,
  getCachedAppTimezone,
} from '../lib/appTimezone';
import {
  INSTANT_REPLAY_SPEEDS,
  INSTANT_REPLAY_PRESET_LABELS,
  acquireInstantReplayLease,
  enabledGoBackPresets,
  fetchInstantReplayAvailability,
  heartbeatInstantReplayLease,
  playheadIsoFromOffset,
  releaseInstantReplayLease,
  resolveInstantReplay,
  seekDeltaSeconds,
  timelineClickToIso,
  timelineFraction,
  type InstantReplayAvailability,
  type InstantReplayLeaseResult,
} from '../lib/instantReplay';

export interface InstantReplayCamera {
  id: string;
  name: string;
  displayName?: string;
  ip_address?: string;
  cameraUid?: string;
  online: boolean;
}

interface InstantReplayModalProps {
  camera: InstantReplayCamera;
  onClose: () => void;
}

const SEEK_STEP = 10;

export default function InstantReplayModal({
  camera,
  onClose,
}: InstantReplayModalProps): React.ReactElement {
  const [timezone, setTimezone] = useState(getCachedAppTimezone);
  const [availability, setAvailability] = useState<InstantReplayAvailability | null>(null);
  const [playlistUrl, setPlaylistUrl] = useState<string | null>(null);
  const [initialSeek, setInitialSeek] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loadingResolve, setLoadingResolve] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [oldestAt, setOldestAt] = useState<string | null>(null);
  const [newestAt, setNewestAt] = useState<string | null>(null);
  const leaseIdRef = useRef<string | null>(null);
  const timelineRef = useRef<HTMLDivElement>(null);

  const {
    videoRef,
    loading,
    error,
    isPlaying,
    currentTime,
    play,
    pause,
    togglePlayPause,
    seek,
    setPlaybackRate,
  } = usePlaybackHLS(playlistUrl, initialSeek);

  useEffect(() => {
    let cancelled = false;
    void ensureAppTimezone().then((tz) => {
      if (!cancelled) setTimezone(tz);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let heartbeatTimer: number | undefined;
    let availPollTimer: number | undefined;

    const startHeartbeat = (leaseId: string) => {
      leaseIdRef.current = leaseId;
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      heartbeatTimer = window.setInterval(() => {
        const lid = leaseIdRef.current;
        if (!lid) return;
        void heartbeatInstantReplayLease(camera, lid);
      }, 25000);
    };

    (async () => {
      setLoadingResolve(true);
      setMessage(null);
      try {
        // Live View may already hold the shared buffer; don't block the modal forever
        // if many concurrent lease POSTs saturate the browser connection pool.
        const lease = await Promise.race([
          acquireInstantReplayLease(camera),
          new Promise<InstantReplayLeaseResult>((resolve) => {
            window.setTimeout(
              () => resolve({ ok: false, error: 'lease_timeout' }),
              12000,
            );
          }),
        ]);
        if (cancelled) return;
        if (lease.ok && lease.leaseId) {
          startHeartbeat(lease.leaseId);
        } else {
          // Retry lease in background (buffer may already be running for this camera).
          void acquireInstantReplayLease(camera).then((retry) => {
            if (cancelled || !retry.ok || !retry.leaseId) return;
            startHeartbeat(retry.leaseId);
          });
        }

        const segs = lease.segmentCount ?? 0;
        if (lease.bufferStarted && segs < 3) {
          await new Promise((r) => setTimeout(r, 2500));
        }
        if (cancelled) return;

        const avail = await fetchInstantReplayAvailability(camera);
        if (cancelled) return;
        setAvailability(avail);
        setOldestAt(avail.oldestAvailableAt ?? null);
        setNewestAt(avail.newestAvailableAt ?? null);

        const resolved = await resolveInstantReplay({ camera, secondsAgo: 30 });
        if (cancelled) return;
        if (!resolved.ok || !resolved.playlistUrl) {
          setMessage(
            resolved.error ||
              'Recent footage is not available for the selected time.',
          );
          setPlaylistUrl(null);
        } else {
          setOldestAt(resolved.oldestAvailableAt ?? avail.oldestAvailableAt ?? null);
          setNewestAt(resolved.newestAvailableAt ?? avail.newestAvailableAt ?? null);
          setInitialSeek(resolved.offsetSeconds ?? 0);
          setPlaylistUrl(resolved.playlistUrl);
          setMessage(null);
        }

        // Refresh preset enablement as the demand-driven buffer grows.
        availPollTimer = window.setInterval(() => {
          void fetchInstantReplayAvailability(camera).then((next) => {
            if (cancelled) return;
            setAvailability(next);
            setOldestAt((prev) => next.oldestAvailableAt ?? prev);
            setNewestAt((prev) => next.newestAvailableAt ?? prev);
          });
        }, 5000);
      } catch (e) {
        if (!cancelled) {
          setMessage(e instanceof Error ? e.message : 'Instant Replay failed to load');
        }
      } finally {
        // Always clear loading — Strict Mode cancel must not leave the overlay stuck.
        setLoadingResolve(false);
      }
    })();

    return () => {
      cancelled = true;
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      if (availPollTimer) window.clearInterval(availPollTimer);
      const lid = leaseIdRef.current;
      leaseIdRef.current = null;
      if (lid) void releaseInstantReplayLease(camera, lid);
      setPlaylistUrl(null);
    };
  }, [camera.id, camera.cameraUid]);

  useEffect(() => {
    setPlaybackRate(speed);
  }, [speed, setPlaybackRate]);

  const presets = useMemo(() => {
    const all = availability?.config?.presetsSeconds ?? [10, 30, 60, 300];
    if (availability?.presetsSeconds) {
      const enabledSet = new Set(availability.presetsSeconds);
      return all.map((seconds) => ({
        seconds,
        label: INSTANT_REPLAY_PRESET_LABELS[seconds] ?? `${seconds}s`,
        enabled: enabledSet.has(seconds),
      }));
    }
    const recent =
      availability?.recentAvailableSeconds ?? availability?.availableSeconds ?? 0;
    return enabledGoBackPresets(recent, all);
  }, [availability]);

  const playheadIso = playheadIsoFromOffset(oldestAt, currentTime);
  const fraction = timelineFraction(oldestAt, newestAt, playheadIso);

  const applyResolve = useCallback(
    async (opts: { secondsAgo?: number; at?: string }) => {
      setLoadingResolve(true);
      setMessage(null);
      try {
        const resolved = await resolveInstantReplay({ camera, ...opts });
        const avail = await fetchInstantReplayAvailability(camera);
        setAvailability(avail);
        if (!resolved.ok || !resolved.playlistUrl) {
          setMessage(
            resolved.error ||
              'Recent footage is not available for the selected time.',
          );
          return;
        }
        setOldestAt(resolved.oldestAvailableAt ?? avail.oldestAvailableAt ?? null);
        setNewestAt(resolved.newestAvailableAt ?? avail.newestAvailableAt ?? null);
        // Remount player with new seek by clearing then setting URL
        setPlaylistUrl(null);
        setInitialSeek(resolved.offsetSeconds ?? 0);
        // next tick
        window.setTimeout(() => {
          setPlaylistUrl(resolved.playlistUrl!);
        }, 0);
      } catch (e) {
        setMessage(e instanceof Error ? e.message : 'Failed to resolve replay');
      } finally {
        setLoadingResolve(false);
      }
    },
    [camera],
  );

  const onTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!oldestAt || !newestAt || !timelineRef.current) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const f = (e.clientX - rect.left) / Math.max(1, rect.width);
    const at = timelineClickToIso(oldestAt, newestAt, f);
    void applyResolve({ at });
  };

  const identity =
    camera.ip_address || camera.cameraUid || camera.id;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/75 p-3 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Instant Replay"
      data-instant-replay="true"
    >
      <div className="relative flex w-full max-w-5xl max-h-[95vh] flex-col overflow-hidden rounded-lg bg-gray-950 text-white shadow-2xl ring-1 ring-gray-700">
        <div className="flex items-start justify-between gap-3 border-b border-gray-800 px-4 py-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-300">
              Instant Replay
            </p>
            <h2 className="truncate text-lg font-bold">{cameraTileLabel(camera)}</h2>
            <p className="truncate text-xs text-gray-400">{identity}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="inline-flex items-center gap-1.5 rounded bg-emerald-600 px-3 py-1.5 text-sm font-semibold hover:bg-emerald-500"
              data-testid="return-to-live"
            >
              <RotateCcw size={14} />
              Return to Live
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded p-1.5 text-gray-300 hover:bg-white/10"
              aria-label="Close Instant Replay"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="relative aspect-video w-full bg-black">
          <video
            ref={videoRef}
            className="absolute inset-0 h-full w-full object-contain"
            playsInline
            muted
            controls={false}
          />
          {(loading || loadingResolve) && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 text-sm text-gray-200">
              Loading recent footage…
            </div>
          )}
          {(message || error) && !loading && !loadingResolve && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/70 px-6 text-center text-sm text-amber-100">
              {message || error}
            </div>
          )}
        </div>

        <div className="space-y-3 border-t border-gray-800 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-300">
            <span>
              Playhead:{' '}
              <span className="font-mono text-white">
                {playheadIso ? formatPlaybackClock(playheadIso, timezone) : '--:--:--'}
              </span>
            </span>
            <span className="text-gray-600">|</span>
            <span>
              Window:{' '}
              {oldestAt ? formatPlaybackClock(oldestAt, timezone) : '—'}
              {' → '}
              {newestAt ? formatPlaybackClock(newestAt, timezone) : '—'}
            </span>
            <span className="text-gray-600">({timezone})</span>
          </div>

          <div
            ref={timelineRef}
            role="slider"
            aria-label="Recent replay timeline"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(fraction * 100)}
            tabIndex={0}
            className="relative h-8 cursor-pointer rounded bg-gray-800"
            onClick={onTimelineClick}
            data-testid="instant-replay-timeline"
          >
            <div
              className="absolute inset-y-1 left-0 rounded bg-amber-500/40"
              style={{ width: '100%' }}
            />
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-white"
              style={{ left: `${fraction * 100}%` }}
            />
            <div className="pointer-events-none absolute inset-x-2 top-1 flex justify-between text-[10px] text-gray-400">
              <span>oldest</span>
              <span>now</span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-gray-400">Go back:</span>
            {presets.map((p) => (
              <button
                key={p.seconds}
                type="button"
                disabled={!p.enabled}
                onClick={() => void applyResolve({ secondsAgo: p.seconds })}
                className={`rounded px-2 py-1 text-xs font-medium ${
                  p.enabled
                    ? 'bg-gray-700 text-white hover:bg-gray-600'
                    : 'cursor-not-allowed bg-gray-900 text-gray-600'
                }`}
                data-testid={`go-back-${p.seconds}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={togglePlayPause}
              className="inline-flex items-center gap-1 rounded bg-gray-700 px-3 py-1.5 text-sm hover:bg-gray-600"
              data-testid="ir-play-pause"
            >
              {isPlaying ? <Pause size={14} /> : <Play size={14} />}
              {isPlaying ? 'Pause' : 'Play'}
            </button>
            <button
              type="button"
              onClick={() => seek(Math.max(0, currentTime + seekDeltaSeconds('backward', SEEK_STEP)))}
              className="inline-flex items-center gap-1 rounded bg-gray-700 px-2 py-1.5 text-sm hover:bg-gray-600"
              data-testid="ir-seek-back"
            >
              <SkipBack size={14} />
              -{SEEK_STEP}s
            </button>
            <button
              type="button"
              onClick={() => seek(currentTime + seekDeltaSeconds('forward', SEEK_STEP))}
              className="inline-flex items-center gap-1 rounded bg-gray-700 px-2 py-1.5 text-sm hover:bg-gray-600"
              data-testid="ir-seek-forward"
            >
              <SkipForward size={14} />
              +{SEEK_STEP}s
            </button>
            <div className="ml-auto flex items-center gap-1">
              <span className="text-xs text-gray-400">Speed</span>
              {INSTANT_REPLAY_SPEEDS.map((rate) => (
                <button
                  key={rate}
                  type="button"
                  onClick={() => {
                    setSpeed(rate);
                    setPlaybackRate(rate);
                    play();
                  }}
                  className={`rounded px-2 py-1 text-xs ${
                    speed === rate
                      ? 'bg-amber-600 text-white'
                      : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                  }`}
                  data-testid={`ir-speed-${rate}`}
                >
                  {rate}x
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
