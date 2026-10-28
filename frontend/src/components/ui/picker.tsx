import * as React from 'react';
import { Popover as PopoverPrimitive } from 'radix-ui';
import { CheckIcon, ChevronDownIcon, SearchIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface PickerOption {
  value: string;
  label: string;
  /** Muted text on the right of the option (a count, a date…). */
  hint?: string;
}

/** Case- and diacritic-insensitive text for the search box (Vietnamese names). */
function fold(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
}

interface PickerProps {
  value: string;
  onChange: (value: string) => void;
  options: PickerOption[];
  /** Trigger text when no option matches `value`. */
  placeholder: string;
  'aria-label': string;
  /** Show a search box (default: when there are more than 8 options). */
  searchable?: boolean;
  searchPlaceholder?: string;
  disabled?: boolean;
  className?: string;
  /** Replaces the trigger's default look (e.g. a page title). */
  triggerClassName?: string;
  contentClassName?: string;
  align?: 'start' | 'end';
  'data-testid'?: string;
}

/** A popover list with search and keyboard support, in place of a long native <select>. */
export function Picker({
  value, onChange, options, placeholder, searchable, searchPlaceholder = 'Tìm…', disabled, className, triggerClassName, contentClassName, align = 'start', ...rest
}: PickerProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [active, setActive] = React.useState(0);
  const listId = React.useId();
  const listRef = React.useRef<HTMLDivElement>(null);
  const showSearch = searchable ?? options.length > 8;
  const selected = options.find(option => option.value === value);
  const shown = React.useMemo(() => {
    const needle = fold(query.trim());
    return needle ? options.filter(option => fold(option.label).includes(needle)) : options;
  }, [options, query]);

  const openChange = (next: boolean) => {
    setOpen(next);
    if (next) {
      setQuery('');
      setActive(Math.max(0, options.findIndex(option => option.value === value)));
    }
  };
  const choose = (option: PickerOption) => {
    onChange(option.value);
    setOpen(false);
  };
  React.useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [active, open]);
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive(index => (shown.length ? (index + step + shown.length) % shown.length : 0));
    } else if (event.key === 'Enter' && shown[active]) {
      event.preventDefault();
      choose(shown[active]);
    }
  };

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={openChange}>
      <PopoverPrimitive.Trigger asChild disabled={disabled}>
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-label={rest['aria-label']}
          data-testid={rest['data-testid']}
          className={cn(
            triggerClassName ?? 'h-9 rounded-md border border-input bg-card pl-3 pr-2 text-sm shadow-xs hover:bg-muted/50',
            'inline-flex min-w-0 items-center justify-between gap-2 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50',
            className,
          )}
        >
          <span className={cn('min-w-0 truncate', !selected && 'text-muted-foreground')}>{selected?.label ?? placeholder}</span>
          <ChevronDownIcon className={cn('size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align={align}
          sideOffset={6}
          onKeyDown={onKeyDown}
          className={cn(
            'z-50 flex max-h-[min(24rem,var(--radix-popover-content-available-height))] w-[max(var(--radix-popover-trigger-width),16rem)] flex-col overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-lg outline-none animate-in fade-in-0 zoom-in-95',
            contentClassName,
          )}
        >
          {showSearch && (
            <div className="relative border-b">
              <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <input
                autoFocus
                value={query}
                onChange={event => { setQuery(event.target.value); setActive(0); }}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                aria-controls={listId}
                className="h-10 w-full bg-transparent pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground"
              />
            </div>
          )}
          <div ref={listRef} id={listId} role="listbox" aria-label={rest['aria-label']} className="min-h-0 flex-1 overflow-y-auto p-1">
            {shown.length === 0 && <p className="px-3 py-6 text-center text-sm text-muted-foreground">Không tìm thấy.</p>}
            {shown.map((option, index) => {
              const isSelected = option.value === value;
              return (
                <div
                  key={option.value}
                  role="option"
                  aria-selected={isSelected}
                  data-index={index}
                  onPointerMove={() => setActive(index)}
                  onClick={() => choose(option)}
                  className={cn(
                    'flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm',
                    index === active && 'bg-accent text-accent-foreground',
                  )}
                >
                  <CheckIcon className={cn('size-4 shrink-0 text-primary', !isSelected && 'invisible')} />
                  <span className={cn('min-w-0 flex-1 truncate', isSelected && 'font-medium')}>{option.label}</span>
                  {option.hint && <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{option.hint}</span>}
                </div>
              );
            })}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
