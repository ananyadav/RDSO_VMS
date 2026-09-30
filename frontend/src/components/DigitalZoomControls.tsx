import React from 'react';
import { ZoomIn, ZoomOut, Maximize } from 'lucide-react';
import {
  DIGITAL_ZOOM_MAX,
  DIGITAL_ZOOM_MIN,
  digitalZoomLabel,
  type DigitalZoomState,
} from '../lib/digitalZoom';

interface DigitalZoomControlsProps {
  zoom: DigitalZoomState;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Client-side digital zoom controls (RDSO 18.2.21 / 18.2.22).
 * Distinct from LivePtzPad optical zoom — never sends PTZ commands.
 */
export default function DigitalZoomControls({
  zoom,
  onZoomIn,
  onZoomOut,
  onReset,
  disabled = false,
  className = '',
}: DigitalZoomControlsProps) {
  const atMin = zoom.scale <= DIGITAL_ZOOM_MIN;
  const atMax = zoom.scale >= DIGITAL_ZOOM_MAX;

  return (
    <div
      className={`flex items-center gap-1.5 rounded-lg border border-white/15 bg-black/55 backdrop-blur-sm px-2 py-1.5 ${className}`}
      data-testid="digital-zoom-controls"
      data-digital-zoom="true"
      title="Digital zoom — enlarges the displayed stream only; does not move the camera"
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <span className="text-[10px] uppercase tracking-wide text-gray-300 px-1 whitespace-nowrap">
        Digital
      </span>
      <button
        type="button"
        data-testid="digital-zoom-out"
        title="Digital zoom out"
        aria-label="Digital zoom out"
        disabled={disabled || atMin}
        onClick={(e) => {
          e.stopPropagation();
          onZoomOut();
        }}
        className="h-9 w-9 rounded-md bg-black/40 hover:bg-blue-600/90 disabled:opacity-40 text-white flex items-center justify-center border border-white/10"
      >
        <ZoomOut size={16} />
      </button>
      <span
        className="min-w-[2.75rem] text-center text-xs font-semibold text-white tabular-nums"
        data-testid="digital-zoom-label"
      >
        {digitalZoomLabel(zoom.scale)}
      </span>
      <button
        type="button"
        data-testid="digital-zoom-in"
        title="Digital zoom in"
        aria-label="Digital zoom in"
        disabled={disabled || atMax}
        onClick={(e) => {
          e.stopPropagation();
          onZoomIn();
        }}
        className="h-9 w-9 rounded-md bg-black/40 hover:bg-blue-600/90 disabled:opacity-40 text-white flex items-center justify-center border border-white/10"
      >
        <ZoomIn size={16} />
      </button>
      <button
        type="button"
        data-testid="digital-zoom-reset"
        title="Reset digital zoom to 1×"
        aria-label="Reset digital zoom"
        disabled={disabled || atMin}
        onClick={(e) => {
          e.stopPropagation();
          onReset();
        }}
        className="h-9 w-9 rounded-md bg-black/40 hover:bg-blue-600/90 disabled:opacity-40 text-white flex items-center justify-center border border-white/10"
      >
        <Maximize size={16} />
      </button>
    </div>
  );
}
