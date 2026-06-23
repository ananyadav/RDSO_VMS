import React, { useRef } from 'react';

// --- MOCK EVENT DATA ---
// We've added a 'type' to each event for color coding.
const events = [
  { start: 10, end: 15, type: 'motion' },
  { start: 25, end: 40, type: 'motion' },
  { start: 60, end: 62, type: 'person' },
  { start: 68, end: 70, type: 'vehicle' },
  { start: 80, end: 95, type: 'motion' },
];

// --- COLOR MAPPING ---
// Maps event types to specific Tailwind CSS background colors. Easy to extend!
const colorMap = {
  person: 'bg-blue-500',
  motion: 'bg-orange-500',
  vehicle: 'bg-purple-500',
  default: 'bg-gray-500',
};

export default function Timeline({ position, onScrub, onEventClick }) {
  const timelineRef = useRef(null);

  // Handles scrubbing the main timeline bar
  const handleScrub = (e) => {
    if (!timelineRef.current) return;
    const rect = timelineRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const width = rect.width;
    const newPosition = Math.max(0, Math.min(100, (x / width) * 100));
    onScrub(newPosition);
  };

  const handleMouseDown = (e) => {
    handleScrub(e);
    const onMouseMove = (moveEvent) => handleScrub(moveEvent);
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };

  return (
    <div className="flex flex-col space-y-2 select-none">
      {/* Timeline Bar */}
      <div
        ref={timelineRef}
        className="w-full h-8 bg-gray-700 rounded-md cursor-pointer relative"
        onMouseDown={handleMouseDown}
      >
        {/* Render Recorded Events */}
        {events.map((event, index) => {
          const bgColor = colorMap[event.type] || colorMap.default;
          return (
            <div
              key={index}
              className={`absolute h-full rounded opacity-75 hover:opacity-100 cursor-pointer transition-opacity`}
              style={{ left: `${event.start}%`, width: `${event.end - event.start}%` }}
              onClick={(e) => {
                e.stopPropagation(); // Prevents the main timeline scrub from firing
                onEventClick(event); // Fire the new event click handler
              }}
            >
              {/* Add a subtle inner div for better styling */}
              <div className={`h-full w-full ${bgColor} rounded`}></div>
            </div>
          );
        })}

        {/* Scrub Handle */}
        <div className="absolute top-0 h-full w-1 bg-yellow-400 pointer-events-none" style={{ left: `${position}%` }}>
          <div className="w-3 h-3 bg-yellow-400 rounded-full absolute -top-1 -translate-x-1/2 shadow-lg" />
        </div>
      </div>

      {/* Time Markers */}
      <div className="flex justify-between text-xs text-gray-400">
        <span>00:00</span><span>03:00</span><span>06:00</span><span>09:00</span>
        <span>12:00</span><span>15:00</span><span>18:00</span><span>21:00</span>
        <span>24:00</span>
      </div>
    </div>
  );
}