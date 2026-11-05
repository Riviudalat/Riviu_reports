import { useEffect, useState } from 'react';
import { ArrowUpIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

// Shown once the page has scrolled about two screens down.
const SHOW_AFTER_PX = 900;

/** A round "back to top" button at the bottom right once the page is scrolled down. */
export function BackToTop() {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const update = () => setShown(window.scrollY > SHOW_AFTER_PX);
    update();
    window.addEventListener('scroll', update, { passive: true });
    return () => window.removeEventListener('scroll', update);
  }, []);
  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      aria-label="Lên đầu trang"
      title="Lên đầu trang"
      tabIndex={shown ? 0 : -1}
      aria-hidden={!shown}
      data-testid="back-to-top"
      className={cn(
        // Phones: above the sticky Tổng bar at the bottom of the list.
        'fixed bottom-24 right-4 z-40 flex size-11 items-center justify-center rounded-full border bg-card text-foreground shadow-lg transition-all hover:bg-muted lg:bottom-6 lg:right-6',
        shown ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-3 opacity-0',
      )}
    >
      <ArrowUpIcon className="size-5" />
    </button>
  );
}
