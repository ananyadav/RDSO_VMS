import React, { useCallback, useRef } from 'react';
import { ArrowUp, ArrowDown, ArrowLeft, ArrowRight, ZoomIn, ZoomOut, Square } from 'lucide-react';
import { usePtzHoldControl } from '../hooks/usePtzHoldControl';

interface LivePtzPadProps {
  cameraId: string;
  online: boolean;
  /** Compact pad for grid tiles; larger for fullscreen. */
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * Live View PTZ pad (pan/tilt/zoom/stop only — no presets).
 * Commands target the given cameraId; stops on release and unmount via usePtzHoldControl.
 */
export default function LivePtzPad({
  cameraId,
  online,
  size = 'sm',
  className = '',
}: LivePtzPadProps) {
  const { onMoveStart, onMoveStop, disabled } = usePtzHoldControl(cameraId, {
    enabled: true,
    online,
    speed: 2,
  });
  const activeRef = useRef(false);

  const bindPress = useCallback(
    (direction: string) => ({
      onMouseDown: (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled) return;
        activeRef.current = true;
        void onMoveStart(direction);
      },
      onMouseUp: (e: React.MouseEvent) => {
        e.stopPropagation();
        if (!activeRef.current) return;
        activeRef.current = false;
        void onMoveStop();
      },
      onMouseLeave: () => {
        if (!activeRef.current) return;
        activeRef.current = false;
        void onMoveStop();
      },
      onTouchStart: (e: React.TouchEvent) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled) return;
        activeRef.current = true;
        void onMoveStart(direction);
      },
      onTouchEnd: (e: React.TouchEvent) => {
        e.stopPropagation();
        if (!activeRef.current) return;
        activeRef.current = false;
        void onMoveStop();
      },
    }),
    [disabled, onMoveStart, onMoveStop],
  );

  const btn =
    size === 'md'
      ? 'h-11 w-11 rounded-md'
      : 'h-8 w-8 rounded';
  const icon = size === 'md' ? 18 : 14;
  const base =
    'bg-black/55 hover:bg-blue-600/90 disabled:opacity-40 text-white flex items-center justify-center transition-colors select-none touch-none backdrop-blur-sm border border-white/10';

  const Btn = ({
    direction,
    children,
    title,
  }: {
    direction: string;
    children: React.ReactNode;
    title: string;
  }) => (
    <button
      type="button"
      disabled={disabled}
      title={title}
      aria-label={title}
      className={`${base} ${btn}`}
      {...bindPress(direction)}
    >
      {children}
    </button>
  );

  return (
    <div
      className={`pointer-events-auto flex flex-col items-center gap-1 ${className}`}
      data-testid="live-ptz-pad"
      data-camera-id={cameraId}
      onDoubleClick={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="grid grid-cols-3 grid-rows-3 gap-0.5">
        <div />
        <Btn direction="up" title="Tilt up">
          <ArrowUp size={icon} />
        </Btn>
        <div />
        <Btn direction="left" title="Pan left">
          <ArrowLeft size={icon} />
        </Btn>
        <Btn direction="home" title="Stop">
          <Square size={icon - 2} className="fill-current" />
        </Btn>
        <Btn direction="right" title="Pan right">
          <ArrowRight size={icon} />
        </Btn>
        <div />
        <Btn direction="down" title="Tilt down">
          <ArrowDown size={icon} />
        </Btn>
        <div />
      </div>
      <div className="flex gap-0.5">
        <Btn direction="zoom_out" title="Zoom out">
          <ZoomOut size={icon} />
        </Btn>
        <Btn direction="zoom_in" title="Zoom in">
          <ZoomIn size={icon} />
        </Btn>
      </div>
      {!online && (
        <span className="text-[10px] text-amber-200/90">Offline</span>
      )}
    </div>
  );
}
