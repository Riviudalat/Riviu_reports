import { Fragment, useMemo, useState } from 'react';
import { ArrowDownIcon, ArrowUpIcon, SearchIcon, XIcon } from 'lucide-react';
import { Input, Skeleton } from '@/components/ui/misc';
import { formatNumber, formatResultNumber, normalizeVietnameseKey } from '@/lib/format';
import { platformConfig } from '@/lib/platforms';
import { columnTotals, metricColumns, partnerSummary, shortUpdate, type PartnerSummaryRow } from '@/lib/riviuData';
import { cn } from '@/lib/utils';
import { useApp } from '@/state/app';

type SortKey = 'partner' | 'links' | string;

const th = 'sticky top-[var(--app-header-h,0px)] z-10 border-b bg-card px-2.5 py-2.5 text-xs font-medium text-muted-foreground';
const td = 'border-b border-border/60 px-2.5 py-2 align-middle';
const foot = 'sticky bottom-0 border-t bg-muted px-2.5 py-2.5';

function sortValue(row: PartnerSummaryRow, key: SortKey): number | string | null {
  if (key === 'partner') return row.partner;
  if (key === 'links') return row.links;
  return row.totals[key] ?? null;
}

/** Tổng kết: views, likes, comments, saves/reposts and shares per partner for the shown month. */
export function SummaryPanel({ className }: { className?: string }) {
  const table = useApp(state => state.rd.table);
  const platform = useApp(state => state.activePlatform);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'views', desc: true });
  const columns = metricColumns(platform);
  const rows = useMemo(() => (table.state === 'ready' ? table.rows : []), [table]);
  const summary = useMemo(() => partnerSummary(rows, platform), [rows, platform]);
  const shown = useMemo(() => {
    const needle = normalizeVietnameseKey(search);
    const list = needle ? summary.filter(row => normalizeVietnameseKey(row.partner).includes(needle)) : [...summary];
    const direction = sort.desc ? -1 : 1;
    return list.sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      // Unknown totals always sink to the bottom, whatever the direction.
      if (left === null || right === null) return left === right ? a.partner.localeCompare(b.partner, 'vi') : left === null ? 1 : -1;
      if (typeof left === 'string' || typeof right === 'string') return String(left).localeCompare(String(right), 'vi') * direction;
      return (left - right) * direction || a.partner.localeCompare(b.partner, 'vi');
    });
  }, [summary, search, sort]);
  // The month's own totals: each link once, even when it belongs to several partners.
  const monthTotals = useMemo(() => columnTotals(rows, platform), [rows, platform]);
  const toggleSort = (key: SortKey) => setSort(current => (current.key === key ? { key, desc: !current.desc } : { key, desc: key !== 'partner' }));

  const header = (key: SortKey, label: string, align: 'left' | 'right') => {
    const active = sort.key === key;
    const Arrow = sort.desc ? ArrowDownIcon : ArrowUpIcon;
    return (
      <th className={cn(th, align === 'right' ? 'text-right' : 'text-left')} aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
        <button type="button" onClick={() => toggleSort(key)} className={cn('inline-flex items-center gap-1 rounded hover:text-foreground', active && 'text-foreground', align === 'right' && 'flex-row-reverse')}>
          {label}
          <Arrow className={cn('size-3', active ? 'opacity-100' : 'opacity-0')} aria-hidden />
        </button>
      </th>
    );
  };

  return (
    <section className={cn('flex min-h-[24rem] min-w-0 flex-col overflow-clip rounded-xl border bg-card shadow-xs', className)} aria-labelledby="summary-title" data-testid="summary-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-4 py-3">
        <h2 id="summary-title" className="mr-auto whitespace-nowrap text-[15px] font-semibold">
          Tổng kết theo đối tác{' '}
          <span className="font-normal text-muted-foreground" data-testid="summary-count">
            {table.state === 'ready' ? (search.trim() ? `${formatNumber(shown.length)} / ${formatNumber(summary.length)}` : formatNumber(summary.length)) : ''}
          </span>
        </h2>
        <div className="relative w-full min-w-0 sm:w-64">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" className="pl-8 pr-8" placeholder="Tìm đối tác…" value={search} onChange={event => setSearch(event.target.value)} aria-label="Tìm đối tác trong tổng kết" />
          {search && (
            <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground" onClick={() => setSearch('')} aria-label="Xóa tìm kiếm">
              <XIcon className="size-3.5" />
            </button>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 border-t max-lg:overflow-x-auto">
        {table.state === 'loading' && <div className="space-y-2 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>}
        {table.state === 'error' && <p className="px-4 py-16 text-center text-sm text-destructive" role="alert">{table.text}</p>}
        {table.state === 'empty' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Chưa có tháng nào có link {platformConfig(platform).label}.</p>}
        {table.state === 'ready' && (
          <table className="w-full table-fixed max-lg:min-w-[860px] border-separate border-spacing-0 text-[13px]" data-testid="summary-table">
            <colgroup>
              <col className="w-[52px]" />
              <col />
              <col className="w-[76px]" />
              {columns.map(column => <col key={column.key} className={column.key === 'views' ? 'w-[110px]' : 'w-[92px]'} />)}
              <col className="w-[104px]" />
            </colgroup>
            <thead>
              <tr>
                <th className={cn(th, 'pl-4 text-left')}>#</th>
                {header('partner', 'Đối tác', 'left')}
                {header('links', 'Số link', 'right')}
                {columns.map(column => <Fragment key={column.key}>{header(column.key, column.label, 'right')}</Fragment>)}
                <th className={cn(th, 'pr-4 text-left')}>Cập nhật</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 && (
                <tr><td colSpan={columns.length + 4} className="px-4 py-14 text-center text-sm text-muted-foreground">
                  {summary.length ? 'Không có đối tác nào khớp.' : 'Tháng này chưa có link nào gắn đối tác.'}
                </td></tr>
              )}
              {shown.map((row, index) => (
                <tr key={row.partner} className="hover:bg-muted/40" data-testid="summary-row">
                  <td className={cn(td, 'pl-4 tabular-nums text-muted-foreground')}>{index + 1}</td>
                  <td className={cn(td, 'break-words font-medium')}>{row.partner}</td>
                  <td className={cn(td, 'text-right tabular-nums')} data-metric="links">{formatNumber(row.links)}</td>
                  {columns.map(column => (
                    <td key={column.key} className={cn(td, 'text-right font-medium tabular-nums')} data-metric={column.key}>{formatResultNumber(row.totals[column.key])}</td>
                  ))}
                  <td className={cn(td, 'whitespace-nowrap pr-4 text-xs tabular-nums text-muted-foreground')} title={row.updatedAt}>{shortUpdate(row.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="font-semibold" data-testid="summary-totals">
                  <td colSpan={2} className={cn(foot, 'pl-4')}>
                    Tổng cả tháng
                    <span className="ml-1.5 text-xs font-normal text-muted-foreground">(mỗi link tính một lần)</span>
                  </td>
                  <td className={cn(foot, 'text-right tabular-nums')} data-testid="summary-total-links">{formatNumber(rows.length)}</td>
                  {columns.map(column => (
                    <td key={column.key} className={cn(foot, 'text-right tabular-nums')} data-testid={`summary-total-${column.key}`}>{formatResultNumber(monthTotals[column.key])}</td>
                  ))}
                  <td className={foot} />
                </tr>
              </tfoot>
            )}
          </table>
        )}
      </div>
    </section>
  );
}
