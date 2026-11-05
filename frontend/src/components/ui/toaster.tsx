import { CircleAlertIcon, CircleCheckIcon, InfoIcon, Loader2Icon, TriangleAlertIcon } from 'lucide-react';
import { Toaster as Sonner } from 'sonner';

const icon = (Icon: typeof InfoIcon, className: string) => (
  <span className={`flex size-6 items-center justify-center rounded-full ${className}`}>
    <Icon className="size-3.5" strokeWidth={2.5} />
  </span>
);

/**
 * Notifications as small cards in the page's own theme (light by default), not sonner's
 * saturated "rich colours": a tinted icon says the kind, the text stays the page's text colour.
 * Unstyled, so the colours come from the page's tokens and follow its `dark` class.
 */
export function Toaster() {
  return (
    <Sonner
      position="bottom-right"
      closeButton
      gap={8}
      offset={{ bottom: 16, right: 16 }}
      mobileOffset={{ bottom: 12, left: 12, right: 12 }}
      icons={{
        success: icon(CircleCheckIcon, 'bg-success/15 text-success'),
        error: icon(CircleAlertIcon, 'bg-destructive/15 text-destructive'),
        warning: icon(TriangleAlertIcon, 'bg-warning/15 text-warning'),
        info: icon(InfoIcon, 'bg-primary/12 text-primary'),
        loading: icon(Loader2Icon, 'bg-muted text-muted-foreground [&_svg]:animate-spin'),
      }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            'group relative flex w-full items-center gap-3 rounded-xl border bg-card py-2.5 pl-3 pr-9 text-foreground shadow-lg shadow-black/5 sm:w-[340px] dark:shadow-black/30',
          content: 'min-w-0 flex-1',
          title: 'text-[13px] font-medium leading-5',
          description: 'text-xs leading-4 text-muted-foreground',
          icon: 'shrink-0',
          closeButton:
            '!absolute !left-auto !right-2 !top-1/2 !-translate-y-1/2 !transform-none flex size-6 items-center justify-center rounded-md !border-0 !bg-transparent text-muted-foreground opacity-0 transition hover:!bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 [&_svg]:size-3.5',
        },
      }}
    />
  );
}
