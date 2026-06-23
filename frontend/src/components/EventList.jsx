import React, { useState } from 'react';
import { Search, Star, Trash2, Download } from 'lucide-react';
import EventItem from './EventItem';

// --- Bulk Action Bar Component ---
const BulkActionBar = ({ selectedCount, onFavorite, onDelete, onExport }) => (
  <div className="bg-blue-900/50 border border-blue-700 rounded-lg p-3 flex items-center justify-between mx-4 my-2">
    <span className="font-semibold text-white">{selectedCount} event{selectedCount > 1 ? 's' : ''} selected</span>
    <div className="flex items-center space-x-4">
      <button onClick={onFavorite} className="flex items-center gap-2 text-yellow-400 hover:text-yellow-300 text-sm">
        <Star size={16} /> Add to Favorites
      </button>
      <button onClick={onExport} className="flex items-center gap-2 text-gray-300 hover:text-white text-sm">
        <Download size={16} /> Export
      </button>
      <button onClick={onDelete} className="flex items-center gap-2 text-red-500 hover:text-red-400 text-sm">
        <Trash2 size={16} /> Delete
      </button>
    </div>
  </div>
);

export default function EventList({ events, onToggleFavorite, onToggleSelection, onSelectAll, selectedEventIds, onPlayEvent, onFavoriteSelected, onDeleteSelected, onExportSelected }) {
  const [searchTerm, setSearchTerm] = useState('');

  const searchResults = events.filter(event => 
    event.type.toLowerCase().includes(searchTerm.toLowerCase()) ||
    event.camera.toLowerCase().includes(searchTerm.toLowerCase())
  );
  
  const isAllSelected = searchResults.length > 0 && selectedEventIds.size === searchResults.length;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg">
      <div className="p-4 border-b border-gray-700 flex flex-col sm:flex-row justify-between items-center gap-4">
        <div className="flex items-center gap-4 text-sm text-gray-400">
          <span><strong className="text-white">{events.length}</strong> total</span>
          <span>Avg. <strong className="text-white">90.8%</strong> confidence</span>
        </div>
        <div className="relative w-full sm:w-auto">
          <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input 
            type="text" placeholder="Search events..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)}
            className="w-full sm:w-64 bg-gray-900 border border-gray-700 rounded-md py-1.5 pl-9 pr-3 text-white text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
        </div>
      </div>
      
      {selectedEventIds.size > 0 ? (
        <BulkActionBar 
          selectedCount={selectedEventIds.size}
          onFavorite={onFavoriteSelected}
          onDelete={onDeleteSelected}
          onExport={onExportSelected}
        />
      ) : (
        <div className="p-4 border-b border-gray-700">
          <label className="flex items-center space-x-3 cursor-pointer">
            <input type="checkbox" checked={isAllSelected} onChange={onSelectAll} className="checkbox-style" />
            <span className="text-gray-300 text-sm">Select all on page</span>
          </label>
        </div>
      )}
      
      <div className="divide-y divide-gray-700">
        {searchResults.length > 0 ? (
          searchResults.map(event => (
            <EventItem 
              key={event.id}
              event={event}
              isSelected={selectedEventIds.has(event.id)}
              onToggleFavorite={onToggleFavorite}
              onToggleSelection={onToggleSelection}
              onPlayEvent={onPlayEvent}
            />
          ))
        ) : (
          <p className="text-center text-gray-400 p-8">No events match your criteria.</p>
        )}
      </div>
    </div>
  );
}