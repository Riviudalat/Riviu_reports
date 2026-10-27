import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import { canAdmin, useApp } from '@/state/app';

export function PlatformIcon({ platform, className }: { platform: PlatformKey; className?: string }) {
  return <img src={platformConfig(platform).icon} alt="" aria-hidden="true" className={cn('platform-icon size-4 shrink-0', className)} />;
}

/** Admin-only controls are not rendered at all for viewers (hidden, not disabled). */
export function AdminOnly({ children }: { children: ReactNode }) {
  const admin = useApp(state => canAdmin(state));
  return admin ? <>{children}</> : null;
}

export function useIsAdmin(): boolean {
  return useApp(state => canAdmin(state));
}

export function EmptyRow({ colSpan, children, tone = 'muted' }: { colSpan: number; children: ReactNode; tone?: 'muted' | 'error' }) {
  return (
    <tr>
      <td colSpan={colSpan} className={cn('py-12 text-sm', tone === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
        {/* Sticky so the message stays in view when the table is wider than its scroller. */}
        <div className="sticky left-0 w-[min(100%,calc(100vw-3rem))] px-4 text-center">{children}</div>
      </td>
    </tr>
  );
}

export const tableClass = 'w-full border-separate border-spacing-0 text-[13px]';
export const thClass = 'sticky top-0 z-10 whitespace-nowrap border-b bg-muted/80 px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground backdrop-blur';
export const tdClass = 'border-b border-border/60 px-3 py-2 align-top';
