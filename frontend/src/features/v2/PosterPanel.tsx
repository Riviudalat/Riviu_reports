import { useMemo, useState } from 'react';
import { RefreshCwIcon, SearchIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input, Skeleton } from '@/components/ui/misc';
import { Picker, type PickerOption } from '@/components/ui/picker';
import { formatNumber, formatResultNumber } from '@/lib/format';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import { NO_POSTER, columnTotals, comparePosters, filterRows, metricColumns, posterOf, posterReport, shortUpdate, type RdRow } from '@/lib/riviuData';
import { DESKTOP_QUERY, useMediaQuery } from '@/lib/useMedia';
import { cn } from '@/lib/utils';
import { useApp } from '@/state/app';
import { refreshView } from '@/state/riviuData';
import { LinkCards, LinkCell, Partners, ROW_RENDER_LIMIT, StatusPill, WIDE_QUERY, td, th } from './LinkTable';

function PosterRow({ row, platform, links, showUpdated }: { row: RdRow; platform: PlatformKey; links: number; showUpdated: boolean }) {
  const poster = posterOf(row);
  return (
    <tr className="hover:bg-muted/40" data-testid="poster-row">
      <td className={cn(td, 'whitespace-nowrap pl-4 tabular-nums text-muted-foreground')}>{row.ngayAir}</td>
      <td className={cn(td, 'truncate font-medium', poster === NO_POSTER && 'font-normal italic text-muted-foreground')} title={poster}>{poster}</td>
      <td className={td}><LinkCell url={row.link} resolved={row.resolvedLink} exclusive={row.partners.length === 1} /></td>
      {metricColumns(platform).map(column => (
        <td key={column.key} className={cn(td, 'text-right font-medium tabular-nums')} data-metric={column.key}>{formatResultNumber(row[column.key])}</td>
      ))}
      <td className={td}><Partners names={row.partners} /></td>
      <td className={td}><StatusPill row={row} /></td>
      <td className={cn(td, 'text-right tabular-nums')} data-testid="poster-links">{formatNumber(links)}</td>
      {showUpdated && <td className={cn(td, 'whitespace-nowrap pr-4 text-xs tabular-nums text-muted-foreground')} title={row.updatedAt}>{shortUpdate(row.updatedAt)}</td>}
    </tr>
  );
}

/** Báo cáo người đăng (admin): the month's links grouped by who posted them, with each poster's link count. */
export function PosterPanel({ className }: { className?: string }) {
  const table = useApp(state => state.rd.table);
  const platform = useApp(state => state.activePlatform);
  const [search, setSearch] = useState('');
  const [posterFilter, setPosterFilter] = useState('');
  const allRows = useMemo(() => (table.state === 'ready' ? table.rows : []), [table]);
  const { counts, sorted } = useMemo(() => posterReport(allRows), [allRows]);
  const options = useMemo<PickerOption[]>(() => [
    { value: '', label: 'Tất cả người đăng', hint: formatNumber(allRows.length) },
    ...[...counts.keys()]
      .sort(comparePosters)
      .map(name => ({ value: name, label: name, hint: formatNumber(counts.get(name) || 0) })),
  ], [counts, allRows.length]);
  const poster = counts.has(posterFilter) ? posterFilter : '';
  const rows = useMemo(
    () => filterRows(sorted, { search, partner: '', poster }),
    [sorted, poster, search],
  );
  const totals = useMemo(() => columnTotals(rows, platform), [rows, platform]);
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const showUpdated = useMediaQuery(WIDE_QUERY);
  const filtered = Boolean(search.trim() || poster);
  const columns = metricColumns(platform);
  const trailing = showUpdated ? 4 : 3;
  const emptyText = allRows.length ? 'Không có link nào khớp với bộ lọc.' : `Tháng này chưa có link ${platformConfig(platform).label}.`;
  return (
    <section className={cn('flex min-h-[24rem] min-w-0 flex-col overflow-clip rounded-xl border bg-card shadow-xs', className)} aria-labelledby="posters-title" data-testid="poster-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-4 py-3">
        <h2 id="posters-title" className="mr-auto whitespace-nowrap text-[15px] font-semibold">
          Báo cáo người đăng{' '}
          <span className="font-normal text-muted-foreground" data-testid="poster-count">
            {table.state === 'ready' ? `${formatNumber(counts.size)} người · ${formatNumber(filtered ? rows.length : allRows.length)} link` : ''}
          </span>
        </h2>
        <div className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:flex sm:w-auto">
          <Picker
            className="w-full sm:w-56"
            value={poster}
            onChange={setPosterFilter}
            options={options}
            placeholder="Tất cả người đăng"
            searchable
            searchPlaceholder="Tìm người đăng…"
            aria-label="Lọc theo người đăng"
            disabled={options.length <= 1}
            align="end"
            contentClassName="w-72"
          />
          <div className="relative col-span-2 row-start-1 min-w-0 sm:w-64">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input type="search" className="pl-8 pr-8" placeholder="Tìm kênh, link, đối tác…" value={search} onChange={event => setSearch(event.target.value)} aria-label="Tìm trong báo cáo người đăng" />
            {search && (
              <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground" onClick={() => setSearch('')} aria-label="Xóa tìm kiếm">
                <XIcon className="size-3.5" />
              </button>
            )}
          </div>
          <Button variant="outline" size="icon" onClick={() => void refreshView()} title="Tải lại số liệu mới nhất" aria-label="Làm mới">
            <RefreshCwIcon />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 border-t">
        {table.state === 'loading' && <div className="space-y-2 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>}
        {table.state === 'error' && <p className="px-4 py-16 text-center text-sm text-destructive" role="alert">{table.text}</p>}
        {table.state === 'empty' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Chưa có tháng nào có link {platformConfig(platform).label}.</p>}
        {table.state === 'ready' && !desktop && (
          <LinkCards key={`${platform}:${search}:${poster}`} rows={rows} platform={platform} totals={totals} filtered={filtered} emptyText={emptyText} />
        )}
        {table.state === 'ready' && desktop && (
          <table className="w-full table-fixed border-separate border-spacing-0 text-[13px]" data-testid="poster-table">
            <colgroup>
              <col className="w-[92px]" />
              <col className="w-[11%]" />
              <col className="w-[22%]" />
              {columns.map(column => <col key={column.key} className={column.key === 'views' ? 'w-[76px]' : 'w-[66px]'} />)}
              <col className="w-[18%]" />
              <col className="w-[84px]" />
              <col className="w-[72px]" />
              {showUpdated && <col className="w-[88px]" />}
            </colgroup>
            <thead>
              <tr>
                <th className={cn(th, 'pl-4')}>Ngày đăng</th>
                <th className={th}>Người đăng</th>
                <th className={th}>Link</th>
                {columns.map(column => <th key={column.key} className={cn(th, 'text-right')}>{column.label}</th>)}
                <th className={th}>Đối tác</th>
                <th className={th}>Trạng thái</th>
                <th className={cn(th, 'text-right')} title="Số link của người đăng này trong tháng">Tổng link</th>
                {showUpdated && <th className={cn(th, 'pr-4')}>Cập nhật</th>}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={3 + columns.length + trailing} className="px-4 py-14 text-center text-sm text-muted-foreground">{emptyText}</td></tr>
              )}
              {rows.slice(0, ROW_RENDER_LIMIT).map(row => (
                <PosterRow key={row.id} row={row} platform={platform} links={counts.get(posterOf(row)) || 0} showUpdated={showUpdated} />
              ))}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="font-semibold" data-testid="poster-totals">
                  <td colSpan={3} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5 pl-4">Tổng{filtered ? ' (đang lọc)' : ''}</td>
                  {columns.map(column => (
                    <td key={column.key} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5 text-right tabular-nums" data-testid={`poster-total-${column.key}`}>{formatResultNumber(totals[column.key])}</td>
                  ))}
                  <td colSpan={2} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5" />
                  <td className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5 text-right tabular-nums" data-testid="poster-total-links">{formatNumber(rows.length)}</td>
                  {showUpdated && <td className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5" />}
                </tr>
              </tfoot>
            )}
          </table>
        )}
      </div>
      {desktop && rows.length > ROW_RENDER_LIMIT && (
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">Đang hiện {formatNumber(ROW_RENDER_LIMIT)} link đầu; lọc theo người đăng hoặc tìm kiếm để thu hẹp. Tổng vẫn tính trên mọi link khớp.</p>
      )}
    </section>
  );
}
