import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useHistory, useLocation } from 'react-router-dom';

export type SearchParamUpdates = Record<string, string | null | undefined>;

export function readInitialParams(search: string): URLSearchParams {
  const raw = search.startsWith('?') ? search.slice(1) : search;
  return new URLSearchParams(raw);
}

export function useSearchParams() {
  const location = useLocation();
  const history = useHistory();

  const params = useMemo(
    () => readInitialParams(location.search),
    [location.search],
  );

  const setParams = useCallback(
    (updates: SearchParamUpdates, options?: { replace?: boolean }) => {
      const next = readInitialParams(location.search);
      for (const [key, value] of Object.entries(updates)) {
        if (value == null || value === '') next.delete(key);
        else next.set(key, value);
      }
      const search = next.toString();
      const current = location.search.startsWith('?')
        ? location.search.slice(1)
        : location.search;
      if (search === current) return;

      const path = {
        pathname: location.pathname,
        search: search ? `?${search}` : '',
      };
      if (options?.replace) history.replace(path);
      else history.push(path);
    },
    [history, location.pathname, location.search],
  );

  return { params, setParams, pathname: location.pathname };
}

/** Snapshot URL params on first render + track when hydration from async data finished. */
export function useUrlHydration() {
  const { params, setParams, pathname } = useSearchParams();
  const initialParams = useRef<URLSearchParams | null>(null);
  if (initialParams.current === null) {
    initialParams.current = readInitialParams(
      params.toString() ? `?${params.toString()}` : '',
    );
  }
  const hydratedRef = useRef(false);
  const markHydrated = useCallback(() => {
    hydratedRef.current = true;
  }, []);
  return { params, setParams, pathname, initialParams, hydratedRef, markHydrated };
}

/** Push view state into the URL after hydration (replace — no history spam). */
export function useUrlSync(
  hydratedRef: React.RefObject<boolean>,
  setParams: ReturnType<typeof useSearchParams>['setParams'],
  values: SearchParamUpdates,
) {
  const payload = useMemo(() => JSON.stringify(values), [values]);
  useEffect(() => {
    if (!hydratedRef.current) return;
    setParams(JSON.parse(payload) as SearchParamUpdates, { replace: true });
  }, [payload, hydratedRef, setParams]);
}

export function formatUrlDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function parseUrlDate(raw: string | null): Date | null {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return null;
  const parsed = new Date(`${raw}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function paramFlag(raw: string | null, defaultValue = false): boolean {
  if (raw == null || raw === '') return defaultValue;
  return raw === '1' || raw === 'true' || raw === 'yes';
}

export function flagParam(value: boolean): string | null {
  return value ? '1' : null;
}

export function joinList(values: string[]): string | null {
  if (!values.length) return null;
  return values.join(',');
}

export function splitList(raw: string | null): string[] {
  if (!raw) return [];
  return raw.split(',').map((s) => s.trim()).filter(Boolean);
}

export function initialStringParam(
  initialParams: React.RefObject<URLSearchParams>,
  key: string,
  fallback = '',
): string {
  return initialParams.current?.get(key) ?? fallback;
}

export function initialEnumParam<T extends string>(
  initialParams: React.RefObject<URLSearchParams>,
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const raw = initialParams.current?.get(key);
  return raw && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
}
