import React from 'react';
import { Star, Camera } from 'lucide-react';
import { format } from 'date-fns';

const colorMap = {
  Person: 'bg-blue-400', Car: 'bg-purple-400', Dog: 'bg-yellow-400',
  Cat: 'bg-green-400', Bicycle: 'bg-pink-400', Truck: 'bg-indigo-400',
  default: 'bg-gray-400',
};

export default function EventItem({ event, isSelected, onToggleFavorite, onToggleSelection, onPlayEvent }) {
  const eventColor = colorMap[event.type] || colorMap.default;

  return (
    <div 
      className={`flex items-center p-4 transition-colors cursor-pointer ${isSelected ? 'bg-blue-900/50' : 'hover:bg-gray-700/50'}`}
      onClick={() => onPlayEvent(event)}
    >
      <div className="flex items-center gap-4 w-40 shrink-0">
        <input 
          type="checkbox" 
          checked={isSelected} 
          onClick={(e) => e.stopPropagation()} 
          onChange={() => onToggleSelection(event.id)} 
          className="checkbox-style" 
        />
        <div className="flex flex-col items-center">
          <Camera size={24} className="text-gray-400" />
          <span className="text-xs font-bold text-green-400 mt-1">{event.confidence}%</span>
        </div>
      </div>

      <div className="flex-grow flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`w-2.5 h-2.5 rounded-full ${eventColor}`}></span>
            <h4 className="font-bold text-white">{event.type} Detected</h4>
          </div>
          <button 
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite(event.id);
            }} 
            title="Toggle favorite"
          >
            <Star size={18} className={`transition-colors ${event.favorite ? 'text-yellow-400 fill-yellow-400' : 'text-gray-500 hover:text-yellow-400'}`} />
          </button>
        </div>
        <p className="text-sm text-gray-400">
          {event.camera} • {format(new Date(event.timestamp), 'MMM dd, yyyy, hh:mm:ss a')}
        </p>
        <p className="text-xs text-gray-500">{event.duration} seconds</p>
      </div>
    </div>
  );
}