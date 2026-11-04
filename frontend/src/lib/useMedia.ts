import { useEffect, useState } from 'react';

/** Whether a CSS media query matches, following changes (true where matchMedia is missing). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : true));
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(query);
    const listener = () => setMatches(media.matches);
    listener();
    media.addEventListener?.('change', listener);
    return () => media.removeEventListener?.('change', listener);
  }, [query]);
  return matches;
}

/** From 1024px the reports show tables; narrower screens (phones, small tablets) get cards. */
export const DESKTOP_QUERY = '(min-width: 1024px)';
