import React, { useEffect, useState } from 'react';
import { cameraTileLabel, type CameraLabelSource } from '../lib/cameraLabel';
import {
  ensureAppTimezone,
  getCachedAppTimezone,
} from '../lib/appTimezone';
import { formatVideoOverlayDateTime } from '../lib/videoTitleTimeOverlay';

export type VideoTitleTimeOverlayProps = {
  camera: CameraLabelSource | null | undefined;
  /** Compact for grid tiles; larger for fullscreen. */
  size?: 'sm' | 'md';
  /** When false, only date/time (avoids duplicate title next to chrome). */
  showTitle?: boolean;
  className?: string;
  /** Corner placement — keep clear of PTZ / digital-zoom controls. */
  position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
};

const POSITION_CLASS: Record<NonNullable<VideoTitleTimeOverlayProps['position']>, string> = {
  'top-left': 'top-2 left-2',
  'top-right': 'top-2 right-2',
  'bottom-left': 'bottom-2 left-2',
  'bottom-right': 'bottom-2 right-2',
};

/**
 * RDSO 18.2.29 — client-side title + date/time superimposed on live video.
 * Does not burn text into the stream; pointer-events none so PTZ/zoom stay usable.
 */
export default function VideoTitleTimeOverlay({
  camera,
  size = 'sm',
  showTitle = true,
  className = '',
  position = 'bottom-right',
}: VideoTitleTimeOverlayProps) {
  const [now, setNow] = useState(() => new Date());
  const [timeZone, setTimeZone] = useState(() => getCachedAppTimezone());
  const title = cameraTileLabel(camera);
  const cameraKey = `${camera?.displayName || ''}|${camera?.name || ''}|${camera?.ip_address || camera?.ipAddress || ''}`;

  useEffect(() => {
    let cancelled = false;
    void ensureAppTimezone().then((tz) => {
      if (!cancelled) setTimeZone(tz);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setNow(new Date());
  }, [cameraKey]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const { date, time } = formatVideoOverlayDateTime(now, timeZone);
  const textSize = size === 'md' ? 'text-sm sm:text-base' : 'text-[10px] sm:text-xs';
  const titleSize = size === 'md' ? 'text-base sm:text-lg' : 'text-xs sm:text-sm';

  return (
    <div
      className={`absolute z-[15] max-w-[85%] pointer-events-none select-none ${POSITION_CLASS[position]} ${className}`}
      data-testid="video-title-time-overlay"
      data-rdso="18.2.29"
      aria-hidden="true"
    >
      <div className="rounded px-1.5 py-0.5 sm:px-2 sm:py-1 bg-black/55 text-white shadow-sm backdrop-blur-[2px]">
        {showTitle && (
          <div className={`font-semibold truncate leading-tight ${titleSize}`} data-testid="video-overlay-title">
            {title}
          </div>
        )}
        <div
          className={`font-mono tabular-nums text-gray-100 leading-tight whitespace-nowrap ${textSize}`}
          data-testid="video-overlay-datetime"
        >
          <span data-testid="video-overlay-date">{date}</span>
          <span className="mx-1 opacity-70">·</span>
          <span data-testid="video-overlay-time">{time}</span>
        </div>
      </div>
    </div>
  );
}
