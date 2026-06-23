import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';
import type { LocationBuilding, LocationSite } from '../constants/corporateFloors';

export function useLocations() {
  const [sites, setSites] = useState<LocationSite[]>([]);
  const [buildings, setBuildings] = useState<LocationBuilding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/locations?includeInactive=true');
      if (!res.ok) throw new Error('Failed to load locations');
      const data = await res.json();
      setSites(data.sites ?? []);
      setBuildings(data.buildings ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load locations');
      setSites([]);
      setBuildings([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { sites, buildings, loading, error, reload };
}
