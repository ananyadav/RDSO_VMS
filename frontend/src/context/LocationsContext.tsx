import React, { createContext, useContext } from 'react';
import { useLocations } from '../hooks/useLocations';
import type { LocationBuilding, LocationSite } from '../constants/corporateFloors';

interface LocationsContextValue {
  sites: LocationSite[];
  buildings: LocationBuilding[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

const LocationsContext = createContext<LocationsContextValue | null>(null);

export function LocationsProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const value = useLocations();
  return <LocationsContext.Provider value={value}>{children}</LocationsContext.Provider>;
}

export function useLocationsContext(): LocationsContextValue {
  const ctx = useContext(LocationsContext);
  if (!ctx) {
    throw new Error('useLocationsContext must be used within LocationsProvider');
  }
  return ctx;
}
