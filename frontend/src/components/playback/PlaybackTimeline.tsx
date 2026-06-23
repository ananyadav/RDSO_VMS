import React, { useMemo, useRef, useState } from 'react';

export interface TimelineRecording {
  sessionId: string;
  startTime: string;
  endTime: string;
  duration: number;
  segmentCount: number;
}

const DAY_MS = 86_400_000;
const HOUR_MARKS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24] as const;
const MIN_BLOCK_PCT = 0.2;

type GapSegment = {
  type: 'gap';
  left: number;
  width: number;
  startMs: number;
  endMs: number;
};

type RecordingSegment = {
  type: 'recording';
  rec: TimelineRecording;
  left: number;
  width: number;
};

type DaySegment = GapSegment | RecordingSegment;

function dayBounds(day: Date): { startMs: number; endMs: number } {
  const start = new Date(day);
  start.setHours(0, 0, 0, 0);
  return { startMs: start.getTime(), endMs: start.getTime() + DAY_MS - 1 };
}

function msToDayPercent(ms: number, dayStartMs: number): number {
  return ((ms - dayStartMs) / DAY_MS) * 100;
}

function formatTimeLabel(isoOrMs: string | number): string {
  try {
    const d = typeof isoOrMs === 'number' ? new Date(isoOrMs) : new Date(isoOrMs);
    return d.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return String(isoOrMs);
  }
}

function formatHour(h: number): string {
  return `${String(h).padStart(2, '0')}:00`;
}

export function buildDaySegments(
  recordings: TimelineRecording[],
  selectedDate: Date,
): DaySegment[] {
  const { startMs: dayStartMs, endMs: dayEndMs } = dayBounds(selectedDate);

  const sorted = [...recordings].sort(
    (a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime(),
  );

  const segments: DaySegment[] = [];
  let cursor = dayStartMs;

  for (const rec of sorted) {
    const recStart = Math.max(dayStartMs, new Date(rec.startTime).getTime());
    const recEnd = Math.min(dayEndMs, new Date(rec.endTime).getTime());
    if (recEnd < recStart) continue;

    if (recStart > cursor) {
      segments.push({
        type: 'gap',
        left: msToDayPercent(cursor, dayStartMs),
        width: msToDayPercent(recStart, dayStartMs) - msToDayPercent(cursor, dayStartMs),
        startMs: cursor,
        endMs: recStart - 1,
      });
    }

    const left = msToDayPercent(recStart, dayStartMs);
    const width = msToDayPercent(recEnd, dayStartMs) - left;
    segments.push({
      type: 'recording',
      rec,
      left,
      width: Math.max(width, MIN_BLOCK_PCT),
    });

    cursor = recEnd + 1;
  }

  if (cursor <= dayEndMs) {
    segments.push({
      type: 'gap',
      left: msToDayPercent(cursor, dayStartMs),
      width: msToDayPercent(dayEndMs, dayStartMs) - msToDayPercent(cursor, dayStartMs),
      startMs: cursor,
      endMs: dayEndMs,
    });
  }

  return segments;
}

export function blockStyle(
  rec: TimelineRecording,
  selectedDate?: Date,
): { left: string; width: string } {
  const day = selectedDate ?? new Date(rec.startTime);
  const { startMs: dayStartMs, endMs: dayEndMs } = dayBounds(day);
  const recStart = Math.max(dayStartMs, new Date(rec.startTime).getTime());
  const recEnd = Math.min(dayEndMs, new Date(rec.endTime).getTime());
  const left = msToDayPercent(recStart, dayStartMs);
  const width = msToDayPercent(recEnd, dayStartMs) - left;
  return { left: `${left}%`, width: `${Math.max(width, MIN_BLOCK_PCT)}%` };
}

export function recordingSeekOffset(
  rec: TimelineRecording,
  selectedDate: Date,
  dayPct: number,
): number {
  const { startMs: dayStartMs, endMs: dayEndMs } = dayBounds(selectedDate);
  const recStart = Math.max(dayStartMs, new Date(rec.startTime).getTime());
  const recEnd = Math.min(dayEndMs, new Date(rec.endTime).getTime());
  if (recEnd <= recStart) return 0;

  const clickMs = dayStartMs + (dayPct / 100) * DAY_MS;
  const clampedMs = Math.max(recStart, Math.min(recEnd, clickMs));
  const offset = (clampedMs - recStart) / 1000;

  if (rec.duration > 0) {
    return Math.max(0, Math.min(rec.duration, offset));
  }
  return Math.max(0, offset);
}

interface PlaybackTimelineProps {
  dateLabel: string;
  selectedDate: Date;
  recordings: TimelineRecording[];
  activeSessionId: string | null;
  playheadPercent: number | null;
  currentTimeLabel: string;
  onBlockClick: (rec: TimelineRecording, dayPercent: number) => void;
  onGapClick: (dayPercent: number) => void;
}

export default function PlaybackTimeline({
  dateLabel,
  selectedDate,
  recordings,
  activeSessionId,
  playheadPercent,
  currentTimeLabel,
  onBlockClick,
  onGapClick,
}: PlaybackTimelineProps): React.ReactElement {
  const trackRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{
    kind: 'recording' | 'gap' | 'track';
    label: string;
    x: number;
  } | null>(null);

  const segments = useMemo(
    () => buildDaySegments(recordings, selectedDate),
    [recordings, selectedDate],
  );

  const clickPercent = (clientX: number): number => {
    if (!trackRef.current) return 0;
    const rect = trackRef.current.getBoundingClientRect();
    return Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
  };

  const handleTrackMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!trackRef.current) return;
    const pct = clickPercent(e.clientX);
    const x = e.clientX - trackRef.current.getBoundingClientRect().left;
    const { startMs: dayStartMs } = dayBounds(selectedDate);
    const hoverMs = dayStartMs + (pct / 100) * DAY_MS;

    const hit = segments.find(
      (seg) =>
        seg.type === 'recording' &&
        pct >= seg.left &&
        pct <= seg.left + seg.width,
    );

    if (hit?.type === 'recording') {
      setHover({
        kind: 'recording',
        label: `${formatTimeLabel(hit.rec.startTime)} – ${formatTimeLabel(hit.rec.endTime)}`,
        x,
      });
      return;
    }

    const gap = segments.find(
      (seg) =>
        seg.type === 'gap' &&
        pct >= seg.left &&
        pct <= seg.left + seg.width,
    );

    if (gap?.type === 'gap') {
      setHover({
        kind: 'gap',
        label: `No recording · ${formatTimeLabel(gap.startMs)} – ${formatTimeLabel(gap.endMs)}`,
        x,
      });
      return;
    }

    setHover({
      kind: 'track',
      label: formatTimeLabel(hoverMs),
      x,
    });
  };

  const handleTrackClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const pct = clickPercent(e.clientX);
    const inRecording = segments.some(
      (seg) =>
        seg.type === 'recording' &&
        pct >= seg.left &&
        pct <= seg.left + seg.width,
    );
    if (!inRecording) {
      onGapClick(pct);
    }
  };

  const dispatchBlockClick = (rec: TimelineRecording, clientX: number) => {
    onBlockClick(rec, clickPercent(clientX));
  };

  const handleBlockPointer = (
    rec: TimelineRecording,
    ev: React.MouseEvent<HTMLButtonElement> | React.PointerEvent<HTMLButtonElement>,
  ) => {
    ev.stopPropagation();
    dispatchBlockClick(rec, ev.clientX);
  };

  return (
    <div className="flex-shrink-0 bg-gray-800 border-t border-gray-700 px-2 py-1.5 select-none">
      <div className="flex items-center justify-between mb-1.5 text-[10px]">
        <span className="text-gray-400 font-medium uppercase tracking-wide">24h Timeline</span>
        <span className="text-gray-300 truncate mx-2">{dateLabel}</span>
        <div className="flex items-center gap-3 text-gray-500">
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-2 rounded-sm bg-blue-600" />
            Recording
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-2 rounded-sm bg-gray-700 border border-gray-600" />
            Gap
          </span>
        </div>
      </div>

      <div
        ref={trackRef}
        role="group"
        aria-label="24-hour playback timeline"
        onPointerLeave={() => setHover(null)}
        onMouseMove={handleTrackMove}
        onClick={handleTrackClick}
        className="relative h-9 w-full rounded border border-gray-600 bg-gray-800 overflow-hidden cursor-default touch-none"
      >
        {HOUR_MARKS.filter((h) => h > 0 && h < 24).map((h) => (
          <div
            key={`grid-${h}`}
            className="absolute top-0 bottom-0 w-px bg-gray-700/60 pointer-events-none z-[1]"
            style={{ left: `${(h / 24) * 100}%` }}
          />
        ))}

        {segments.map((seg, i) => {
            if (seg.type === 'gap') {
              return (
                <button
                  key={`gap-${i}`}
                  type="button"
                  aria-label={`No recording ${formatTimeLabel(seg.startMs)} to ${formatTimeLabel(seg.endMs)}`}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onGapClick(clickPercent(ev.clientX));
                  }}
                  className="absolute top-0 bottom-0 z-[2] bg-gray-700/50 border-x border-gray-600/40 cursor-not-allowed hover:bg-gray-600/40"
                  style={{
                    left: `${seg.left}%`,
                    width: `${Math.max(seg.width, 0)}%`,
                    backgroundImage:
                      'repeating-linear-gradient(-45deg, transparent, transparent 3px, rgba(0,0,0,0.18) 3px, rgba(0,0,0,0.18) 6px)',
                  }}
                  title={`No recording · ${formatTimeLabel(seg.startMs)} – ${formatTimeLabel(seg.endMs)}`}
                />
              );
            }

            const active = seg.rec.sessionId === activeSessionId;
            return (
              <button
                key={seg.rec.sessionId}
                type="button"
                style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
                title={`${formatTimeLabel(seg.rec.startTime)} – ${formatTimeLabel(seg.rec.endTime)}`}
                onPointerUp={(ev) => {
                  if (ev.pointerType === 'mouse' && ev.button !== 0) return;
                  handleBlockPointer(seg.rec, ev);
                }}
                className={`absolute top-0.5 bottom-0.5 z-[5] rounded-sm min-w-[3px] cursor-pointer transition-colors touch-manipulation ${
                  active
                    ? 'bg-blue-400 ring-1 ring-blue-200 shadow-[0_0_8px_rgba(96,165,250,0.45)]'
                    : 'bg-blue-600 hover:bg-blue-500'
                }`}
              />
            );
          })}

        {playheadPercent != null && (
          <div
            className="absolute top-0 bottom-0 z-20 pointer-events-none"
            style={{ left: `${playheadPercent}%` }}
          >
            <div className="absolute -top-0.5 left-1/2 -translate-x-1/2 w-2 h-2 bg-red-500 rotate-45 border border-red-300" />
            <div className="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 w-0.5 bg-red-500" />
          </div>
        )}

        {hover && (
          <div
            className="absolute z-30 bg-gray-900 border border-gray-500 rounded px-2 py-1 text-[11px] pointer-events-none shadow-lg whitespace-nowrap"
            style={{
              left: Math.min(Math.max(hover.x, 72), (trackRef.current?.offsetWidth ?? 300) - 72),
              bottom: 'calc(100% + 4px)',
              transform: 'translateX(-50%)',
              color: hover.kind === 'gap' ? '#9ca3af' : '#fff',
            }}
          >
            {hover.label}
          </div>
        )}
      </div>

      <div className="mt-1 flex justify-between items-center w-full px-0.5">
        {HOUR_MARKS.map((h) => (
          <span
            key={h}
            className="text-[10px] font-mono text-gray-400 leading-none tabular-nums"
          >
            {h === 24 ? '23:59' : formatHour(h)}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-gray-500">
        <span>00:00:00</span>
        <span className="text-red-400 font-semibold text-sm tabular-nums">
          {playheadPercent != null ? currentTimeLabel : '—'}
        </span>
        <span>23:59:59</span>
      </div>
    </div>
  );
}
