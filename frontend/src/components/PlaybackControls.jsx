import { Play, Pause, FastForward, Camera, Download, ChevronLeft, ChevronRight } from 'lucide-react';

export default function PlaybackControls({ isPlaying, playbackSpeed, onPlayPause, onSpeedChange, onDateChange, onAction }) {
  const speeds = [0.5, 1, 2, 4];

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between text-gray-300">
      {/* Left: Playback & Actions */}
      <div className="flex items-center space-x-4">
        <button onClick={onPlayPause} className="text-white p-2 bg-blue-600 hover:bg-blue-700 rounded-full">
          {isPlaying ? <Pause size={20} /> : <Play size={20} />}
        </button>
        <button onClick={() => onAction('Snapshot')} className="flex items-center space-x-2 hover:text-white"><Camera size={18} /><span>Snapshot</span></button>
        <button onClick={() => onAction('Download')} className="flex items-center space-x-2 hover:text-white"><Download size={18} /><span>Download</span></button>
      </div>

      {/* Right: Date & Speed */}
      <div className="flex items-center space-x-6 mt-4 sm:mt-0">
        <div className="flex items-center space-x-2">
          <button onClick={() => onDateChange(-1)} className="p-2 hover:bg-gray-700 rounded-full"><ChevronLeft size={20}/></button>
          <span>Prev Day</span>
          <span className="font-semibold text-white mx-2">|</span>
          <span>Next Day</span>
          <button onClick={() => onDateChange(1)} className="p-2 hover:bg-gray-700 rounded-full"><ChevronRight size={20}/></button>
        </div>
        <div className="flex items-center space-x-2">
          <FastForward size={18} className="text-gray-400" />
          {speeds.map(speed => (
            <button
              key={speed}
              onClick={() => onSpeedChange(speed)}
              className={`px-2 py-0.5 rounded ${playbackSpeed === speed ? 'bg-blue-600 text-white' : 'hover:bg-gray-700'}`}
            >
              {speed}x
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}