import React, { useCallback, useRef } from 'react';
import { ArrowUp, ArrowDown, ArrowLeft, ArrowRight, ZoomIn, ZoomOut, Home } from 'lucide-react';
import Card from './Card';

interface PTZControlsProps {
  speed: number;
  disabled?: boolean;
  onMoveStart: (direction: string) => void;
  onMoveStop: () => void;
}

export default function PTZControls({
  speed,
  disabled = false,
  onMoveStart,
  onMoveStop,
}: PTZControlsProps) {
  const activeRef = useRef(false);

  const bindPress = useCallback(
    (direction: string) => ({
      onMouseDown: (e: React.MouseEvent) => {
        e.preventDefault();
        if (disabled) return;
        activeRef.current = true;
        onMoveStart(direction);
      },
      onMouseUp: () => {
        if (!activeRef.current) return;
        activeRef.current = false;
        onMoveStop();
      },
      onMouseLeave: () => {
        if (!activeRef.current) return;
        activeRef.current = false;
        onMoveStop();
      },
      onTouchStart: (e: React.TouchEvent) => {
        e.preventDefault();
        if (disabled) return;
        activeRef.current = true;
        onMoveStart(direction);
      },
      onTouchEnd: () => {
        if (!activeRef.current) return;
        activeRef.current = false;
        onMoveStop();
      },
    }),
    [disabled, onMoveStart, onMoveStop],
  );

  const DPadButton = ({
    direction,
    children,
  }: {
    direction: string;
    children: React.ReactNode;
  }) => (
    <button
      type="button"
      disabled={disabled}
      className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 rounded-md flex items-center justify-center h-12 w-12 transition-colors select-none touch-none"
      {...bindPress(direction)}
    >
      {children}
    </button>
  );

  return (
    <Card className="flex flex-col gap-3 !p-3">
      <div className="flex flex-col items-center">
        <div className="grid grid-cols-3 grid-rows-3 gap-1.5">
          <div />
          <DPadButton direction="up">
            <ArrowUp size={20} />
          </DPadButton>
          <div />
          <DPadButton direction="left">
            <ArrowLeft size={20} />
          </DPadButton>
          <button
            type="button"
            disabled={disabled}
            className="bg-gray-900/50 hover:bg-gray-700 disabled:opacity-40 rounded-full flex items-center justify-center h-12 w-12 transition-colors select-none touch-none"
            {...bindPress('home')}
            title="Stop / home"
          >
            <Home size={18} className="text-gray-400" />
          </button>
          <DPadButton direction="right">
            <ArrowRight size={20} />
          </DPadButton>
          <div />
          <DPadButton direction="down">
            <ArrowDown size={20} />
          </DPadButton>
          <div />
        </div>
        <p className="text-xs text-gray-400 mt-1.5">Hold to move · Speed {speed}</p>
      </div>

      <div className="flex flex-col items-center gap-1">
        <p className="text-xs text-gray-400">Zoom</p>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={disabled}
            className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 rounded-md flex items-center justify-center h-11 w-11 transition-colors select-none touch-none"
            title="Zoom Out"
            {...bindPress('zoom_out')}
          >
            <ZoomOut size={16} />
          </button>
          <button
            type="button"
            disabled={disabled}
            className="bg-gray-700 hover:bg-blue-600 disabled:opacity-40 rounded-md flex items-center justify-center h-11 w-11 transition-colors select-none touch-none"
            title="Zoom In"
            {...bindPress('zoom_in')}
          >
            <ZoomIn size={16} />
          </button>
        </div>
      </div>
    </Card>
  );
}
