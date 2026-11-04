import { formatResultNumber } from '@/lib/format';
import type { PlatformKey } from '@/lib/platforms';
import { metricColumns } from '@/lib/riviuData';
import { cn } from '@/lib/utils';

/** The platform's five metrics side by side, for the phone cards (blank = unknown, shown as "–"). */
export function MetricGrid({ values, platform, className, testPrefix }: {
  values: object;
  platform: PlatformKey;
  className?: string;
  /** data-testid prefix for each value, such as "total-" (gives "total-views"). */
  testPrefix?: string;
}) {
  const record = values as Record<string, unknown>;
  return (
    <dl className={cn('grid grid-cols-5 gap-1 text-center', className)}>
      {metricColumns(platform).map(column => {
        const text = formatResultNumber(record[column.key]);
        return (
          <div key={column.key} className="min-w-0">
            <dt className="truncate text-[11px] leading-4 text-muted-foreground">{column.label}</dt>
            <dd
              className={cn('truncate text-sm font-semibold tabular-nums', !text && 'text-muted-foreground/60')}
              data-metric={column.key}
              data-testid={testPrefix ? `${testPrefix}${column.key}` : undefined}
            >
              {text || '–'}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
