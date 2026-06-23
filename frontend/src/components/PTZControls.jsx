import React from 'react';
import { ArrowUp, ArrowDown, ArrowLeft, ArrowRight, ZoomIn, ZoomOut, Home } from 'lucide-react';
import Card from './Card';

export default function PTZControls({ onMove, onZoom }) {
  // A styled button for the D-pad
  const DPadButton = ({ onClick, children }) => (
    <button
      onClick={onClick}
      className="bg-gray-700 hover:bg-blue-600 rounded-md flex items-center justify-center h-16 w-16 transition-colors"
    >
      {children}
    </button>
  );

  return (
    <Card className="flex flex-col gap-4">
      {/* D-Pad for Movement */}
      <div className="flex flex-col items-center">
        <div className="grid grid-cols-3 grid-rows-3 gap-2">
          <div />
          <DPadButton onClick={() => onMove('up')}><ArrowUp size={24} /></DPadButton>
          <div />
          <DPadButton onClick={() => onMove('left')}><ArrowLeft size={24} /></DPadButton>
          <button onClick={() => onMove('home')} className="bg-gray-900/50 hover:bg-gray-700 rounded-full flex items-center justify-center h-16 w-16 transition-colors col-span-1 row-span-1 col-start-2 row-start-2">
            <Home size={20} className="text-gray-400" />
          </button>
          <DPadButton onClick={() => onMove('right')}><ArrowRight size={24} /></DPadButton>
          <div />
          <DPadButton onClick={() => onMove('down')}><ArrowDown size={24} /></DPadButton>
          <div />
        </div>
        <p className="text-xs text-gray-400 mt-2">PTZ Movement</p>
      </div>

      {/* Zoom Controls */}
      <div className="flex flex-col items-center gap-2">
        <p className="text-xs text-gray-400 mb-1">Zoom</p>
        <div className="flex gap-2">
          <button onClick={() => onZoom(-1)} className="bg-gray-700 hover:bg-blue-600 rounded-md flex items-center justify-center h-16 w-16 transition-colors" title="Zoom Out">
            <ZoomOut size={18} />
          </button>
          <button onClick={() => onZoom(1)} className="bg-gray-700 hover:bg-blue-600 rounded-md flex items-center justify-center h-16 w-16 transition-colors" title="Zoom In">
            <ZoomIn size={18} />
          </button>
        </div>
      </div>
    </Card>
  );
}
