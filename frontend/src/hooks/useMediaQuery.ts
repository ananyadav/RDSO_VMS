import { useEffect, useState } from 'react';

/** Subscribe to a CSS media query; SSR-safe default false until mounted. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    const mq = window.matchMedia(query);
    const sync = () => setMatches(mq.matches);
    sync();
    mq.addEventListener('change', sync);
    return () => mq.removeEventListener('change', sync);
  }, [query]);

  return matches;
}

export function useIsPhoneLayout(): boolean {
  return useMediaQuery('(max-width: 767px)');
}
