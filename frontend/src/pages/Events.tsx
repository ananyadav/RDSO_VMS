import React, { useState, useMemo, useEffect } from 'react';
import toast from 'react-hot-toast';
import { Download } from 'lucide-react';
import {
  useUrlHydration,
  useUrlSync,
  paramFlag,
  flagParam,
  joinList,
  splitList,
  parseUrlDate,
  formatUrlDate,
} from '../hooks/useUrlSearchState';

// --- Component Imports ---
import PageHeader from '../components/PageHeader';
import EventFilter from '../components/EventFilter';
import EventList from '../components/EventList';
import EventPlaybackModal from '../components/EventPlaybackModal';

// --- MOCK DATA ---
const allEvents = [
  { id: 1, type: 'Person', camera: 'Driveway', timestamp: '2025-08-29T11:57:00', confidence: 95, favorite: true, duration: 15 },
  { id: 2, type: 'Car', camera: 'Front Door', timestamp: '2025-08-29T11:34:00', confidence: 88, favorite: false, duration: 8 },
  { id: 3, type: 'Dog', camera: 'Backyard', timestamp: '2025-08-29T11:11:00', confidence: 91, favorite: false, duration: 12 },
  { id: 4, type: 'Cat', camera: 'Backyard', timestamp: '2025-08-29T10:48:00', confidence: 79, favorite: true, duration: 5 },
  { id: 5, type: 'Bicycle', camera: 'Kitchen', timestamp: '2025-08-29T10:25:00', confidence: 82, favorite: false, duration: 22 },
  { id: 6, type: 'Person', camera: 'Garage', timestamp: '2025-08-29T09:15:00', confidence: 98, favorite: false, duration: 18 },
];
const availableCameras = ['Driveway', 'Front Door', 'Backyard', 'Garage', 'Kitchen'];
const availableEventTypes = ['Person', 'Car', 'Dog', 'Cat', 'Bicycle', 'Truck'];
const initialFilters = {
  date: new Date().toISOString().split('T')[0],
  showFavoritesOnly: false,
  cameras: availableCameras,
  eventTypes: availableEventTypes,
};

export default function Events() {
  const { params, setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();

  const [events, setEvents] = useState(allEvents);
  const [filters, setFilters] = useState(() => {
    const dateRaw = initialParams.current?.get('date');
    const dateParsed = dateRaw ? parseUrlDate(dateRaw) : null;
    const camerasRaw = splitList(initialParams.current?.get('cameras') ?? null);
    const typesRaw = splitList(initialParams.current?.get('types') ?? null);
    return {
      date: dateParsed ? formatUrlDate(dateParsed) : initialFilters.date,
      showFavoritesOnly: paramFlag(initialParams.current?.get('favorites') ?? null, false),
      cameras: camerasRaw.length ? camerasRaw : availableCameras,
      eventTypes: typesRaw.length ? typesRaw : availableEventTypes,
    };
  });
  const [selectedEventIds, setSelectedEventIds] = useState(new Set<number>());
  const [playingEvent, setPlayingEvent] = useState<(typeof allEvents)[0] | null>(() => {
    const id = initialParams.current?.get('event');
    if (!id) return null;
    return allEvents.find((e) => String(e.id) === id) ?? null;
  });

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({
      date: filters.date !== initialFilters.date ? filters.date : null,
      favorites: flagParam(filters.showFavoritesOnly),
      cameras:
        filters.cameras.length !== availableCameras.length
          ? joinList(filters.cameras)
          : null,
      types:
        filters.eventTypes.length !== availableEventTypes.length
          ? joinList(filters.eventTypes)
          : null,
      event: playingEvent ? String(playingEvent.id) : null,
    }),
    [filters, playingEvent],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  useEffect(() => {
    const eventId = params.get('event');
    if (!eventId) {
      setPlayingEvent(null);
      return;
    }
    setPlayingEvent(allEvents.find((e) => String(e.id) === eventId) ?? null);
  }, [params]);

  const filteredEvents = useMemo(() => {
    return events.filter(event => {
      if (filters.showFavoritesOnly && !event.favorite) return false;
      if (!filters.cameras.includes(event.camera)) return false;
      if (!filters.eventTypes.includes(event.type)) return false;
      return true;
    });
  }, [events, filters]);
  
  // --- Event Handlers ---
  const handleFilterChange = (newFilters: Partial<typeof initialFilters>) => setFilters(prev => ({ ...prev, ...newFilters }));
  const handleToggleFavorite = (eventId: number) => setEvents(events.map(event => event.id === eventId ? { ...event, favorite: !event.favorite } : event));
  const handleToggleSelection = (eventId: number) => {
    setSelectedEventIds(prev => {
      const newSelection = new Set(prev);
      if (newSelection.has(eventId)) newSelection.delete(eventId);
      else newSelection.add(eventId);
      return newSelection;
    });
  };
  const handleSelectAllOnPage = () => {
    if (selectedEventIds.size === filteredEvents.length) setSelectedEventIds(new Set());
    else setSelectedEventIds(new Set(filteredEvents.map(e => e.id)));
  };
  const handlePlayEvent = (event: typeof allEvents[0]) => setPlayingEvent(event);

  // --- Bulk Action Handlers ---
  const handleFavoriteSelected = () => {
    setEvents(events.map(event => selectedEventIds.has(event.id) ? { ...event, favorite: true } : event));
    toast.success(`${selectedEventIds.size} event(s) added to favorites.`);
    setSelectedEventIds(new Set());
  };
  const onDeleteSelected = () => {
    setEvents(events.filter(event => !selectedEventIds.has(event.id)));
    toast.error(`${selectedEventIds.size} event(s) deleted.`);
    setSelectedEventIds(new Set());
  };
  const onExportSelected = () => {
    toast(`Exporting ${selectedEventIds.size} event(s)... (demo)`);
    setSelectedEventIds(new Set());
  };

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Events"
        subtitle="Review and manage security events"
        rightContent={
          <button className="flex items-center text-sm font-semibold bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md">
            <Download size={16} className="mr-2" />
            Export
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-4">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-1">
          <EventFilter
            filters={filters}
            onFilterChange={handleFilterChange}
            availableCameras={availableCameras}
            availableEventTypes={availableEventTypes}
          />
        </div>
        <div className="lg:col-span-3">
          <EventList
            events={filteredEvents}
            selectedEventIds={selectedEventIds}
            onToggleFavorite={handleToggleFavorite}
            onToggleSelection={handleToggleSelection}
            onSelectAll={handleSelectAllOnPage}
            onPlayEvent={handlePlayEvent}
            onFavoriteSelected={handleFavoriteSelected}
            onDeleteSelected={onDeleteSelected}
            onExportSelected={onExportSelected}
          />
        </div>
      </div>
      </div>

      {playingEvent && (
        <EventPlaybackModal 
          event={playingEvent} 
          onClose={() => setPlayingEvent(null)}
        />
      )}
    </div>
  );
}
