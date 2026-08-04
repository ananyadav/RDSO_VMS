import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch, readJsonResponse } from '../lib/api';
import { readSessionCache, UI_CACHE_TTL_MS, writeSessionCache } from '../lib/sessionCache';
import type { LocationBuilding, LocationSite } from '../constants/corporateFloors';

const LOCATIONS_CACHE_KEY = 'cctv:locations:v1';

type LocationsCache = {
  sites: LocationSite[];
  buildings: LocationBuilding[];
};

export function useLocations() {
  const cached = readSessionCache<LocationsCache>(LOCATIONS_CACHE_KEY, UI_CACHE_TTL_MS);
  const hadCacheRef = useRef(Boolean(cached));
  const [sites, setSites] = useState<LocationSite[]>(() => cached?.sites ?? []);
  const [buildings, setBuildings] = useState<LocationBuilding[]>(() => cached?.buildings ?? []);
  const [loading, setLoading] = useState(() => !cached);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const res = await apiFetch('/api/locations?includeInactive=true&includeStats=true');
      if (!res.ok) throw new Error('Failed to load locations');
      const data = await readJsonResponse<{ sites?: LocationSite[]; buildings?: LocationBuilding[] }>(res);
      const nextSites = data.sites ?? [];
      const nextBuildings = data.buildings ?? [];
      setSites(nextSites);
      setBuildings(nextBuildings);
      writeSessionCache(LOCATIONS_CACHE_KEY, { sites: nextSites, buildings: nextBuildings });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load locations');
      if (!hadCacheRef.current) {
        setSites([]);
        setBuildings([]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { sites, buildings, loading, error, reload };
}
