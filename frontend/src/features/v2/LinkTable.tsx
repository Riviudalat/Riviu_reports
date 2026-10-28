import { useId, useMemo, useState } from 'react';
import { DownloadIcon, RefreshCwIcon, SearchIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input, NativeSelect, Skeleton } from '@/components/ui/misc';
import { formatNumber, formatResultNumber } from '@/lib/format';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import {
  columnTotals, filterRows, lastUpdateText, metricColumns, scanStatusView, shortLink, shortUpdate, type RdRow, type ScanStatusTone,
} from '@/lib/riviuData';
import { scanIsBusy, viewerScanText } from '@/lib/scan';
import { cn } from '@/lib/utils';
import { openModal, useApp } from '@/state/app';
import { monthsOf, refreshView, selectThang } from '@/state/riviuData';

// ---- Report context: which month, its freshness, the month picker and export ----------

/** The calm read-only line while a scan runs (every visitor sees it). */
export function ScanStatePill() {
  const busy = useApp(state => scanIsBusy(state.scan.phase));
  const wait = useApp(state => state.scan.retryWait);
  const runPlatform = useApp(state => state.scan.runContext?.platform);
  const active = useApp(state => state.activePlatform);
  if (!busy) return null;
  const other = runPlatform && runPlatform !== active && (runPlatform === 'tiktok' || runPlatform === 'threads') ? platformConfig(runPlatform).label : '';
  const text = wait ? viewerScanText(wait) : other ? `Đang cập nhật số liệu ${other}…` : viewerScanText(null);
  return (
    <span role="status" data-testid="scan-state" className="inline-flex max-w-full items-center gap-2 rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-accent-foreground">
      <span className="relative flex size-2 shrink-0">
        {!wait && <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary opacity-60" />}
        <span className="relative inline-flex size-2 rounded-full bg-primary" />
      </span>
      <span className="min-w-0">{text}</span>
    </span>
  );
}

export function MonthSelect({ className }: { className?: string }) {
  const id = useId();
  const platform = useApp(state => state.activePlatform);
  const months = useApp(state => monthsOf(state));
  const thang = useApp(state => state.rd.thang[state.activePlatform]);
  const loaded = useApp(state => state.rd.months[state.activePlatform] !== null);
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">Tháng</label>
      <NativeSelect id={id} className="md:w-60" value={thang} disabled={!months.length} onChange={event => void selectThang(event.target.value)} aria-label={`Tháng báo cáo ${platformConfig(platform).label}`} data-testid="month-select">
        {!months.length && <option value="">{loaded ? 'Chưa có tháng nào' : 'Đang tải…'}</option>}
        {months.map(month => <option key={month.thang} value={month.thang}>{month.label} · {formatNumber(month.rows)} link</option>)}
      </NativeSelect>
    </div>
  );
}

export function ReportContext() {
  const platform = useApp(state => state.activePlatform);
  const thang = useApp(state => state.rd.thang[state.activePlatform]);
  const months = useApp(state => monthsOf(state));
  const table = useApp(state => state.rd.table);
  const month = months.find(item => item.thang === thang);
  const rows = table.state === 'ready' ? table.rows : null;
  const updated = useMemo(() => (rows ? lastUpdateText(rows) : ''), [rows]);
  return (
    <section className="flex flex-col gap-4 pb-4 pt-5 md:flex-row md:items-end md:justify-between md:gap-6 md:pt-7" aria-labelledby="report-title">
      <div className="min-w-0">
        <p className="text-xs font-semibold uppercase tracking-wider text-primary">Báo cáo {platformConfig(platform).label}</p>
        <h1 id="report-title" className="mt-1 text-2xl font-semibold tracking-tight sm:text-[26px]">{month?.label || (thang ? thang : 'Chưa có dữ liệu')}</h1>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted-foreground">
          {rows && <span><b className="font-semibold text-foreground">{formatNumber(rows.length)}</b> link</span>}
          {updated && <span data-testid="last-update">{updated}</span>}
          <ScanStatePill />
        </p>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2.5 md:flex md:shrink-0">
        <MonthSelect />
        <Button className="h-9" onClick={() => openModal('export')} disabled={!thang} data-testid="open-export">
          <DownloadIcon /> Xuất báo cáo
        </Button>
      </div>
    </section>
  );
}

// ---- The link table --------------------------------------------------------------------

const STATUS_TONE: Record<ScanStatusTone, string> = {
  success: 'bg-success/12 text-success',
  warning: 'bg-warning/15 text-warning',
  error: 'bg-destructive/12 text-destructive',
  muted: 'bg-muted text-muted-foreground',
};

const th = 'sticky top-0 z-10 whitespace-nowrap border-b bg-card px-3 py-2.5 text-left text-xs font-medium text-muted-foreground';
const td = 'border-b border-border/60 px-3 py-2 align-middle';

function Partners({ names }: { names: string[] }) {
  if (!names.length) return null;
  const shown = names.slice(0, 2);
  return (
    <span className="flex max-w-80 gap-1" title={names.join(', ')}>
      {shown.map(name => <span key={name} className="min-w-0 max-w-36 truncate whitespace-nowrap rounded-md bg-muted px-1.5 py-0.5 text-xs text-foreground/80">{name}</span>)}
      {names.length > shown.length && <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">+{names.length - shown.length}</span>}
    </span>
  );
}

function LinkCell({ url }: { url: string }) {
  const { handle, detail } = shortLink(url);
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" title={url} className="whitespace-nowrap font-medium text-primary hover:underline">
      {handle}{detail && <span className="hidden font-normal text-muted-foreground sm:inline"> · {detail}</span>}
    </a>
  );
}

function Row({ row, platform }: { row: RdRow; platform: PlatformKey }) {
  const status = scanStatusView(row);
  return (
    <tr className="hover:bg-muted/40" data-testid="link-row">
      <td className={cn(td, 'whitespace-nowrap tabular-nums text-muted-foreground')}>{row.ngayAir}</td>
      <td className={cn(td, 'max-w-40 truncate')} title={row.channel}>{row.channel}</td>
      <td className={td}><LinkCell url={row.link} /></td>
      <td className={td}><Partners names={row.partners} /></td>
      {metricColumns(platform).map(column => (
        <td key={column.key} className={cn(td, 'text-right font-medium tabular-nums')} data-metric={column.key}>{formatResultNumber(row[column.key])}</td>
      ))}
      <td className={td}>
        <span className={cn('inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold', STATUS_TONE[status.tone])} title={status.title}>{status.label}</span>
      </td>
      <td className={cn(td, 'whitespace-nowrap text-xs tabular-nums text-muted-foreground')} title={row.updatedAt}>{shortUpdate(row.updatedAt)}</td>
    </tr>
  );
}

const ROW_RENDER_LIMIT = 3000;

export function LinkTablePanel({ className }: { className?: string }) {
  const table = useApp(state => state.rd.table);
  const platform = useApp(state => state.activePlatform);
  const [search, setSearch] = useState('');
  const [partnerFilter, setPartnerFilter] = useState('');
  const partners = useMemo(() => (table.state === 'ready' ? [...table.partners].sort((a, b) => a.localeCompare(b, 'vi')) : []), [table]);
  const partner = partners.includes(partnerFilter) ? partnerFilter : '';
  const rows = useMemo(() => (table.state === 'ready' ? filterRows(table.rows, { search, partner }) : []), [table, search, partner]);
  const totals = useMemo(() => columnTotals(rows, platform), [rows, platform]);
  const total = table.state === 'ready' ? table.rows.length : 0;
  const filtered = Boolean(search.trim() || partner);
  const columns = metricColumns(platform);
  const colSpan = 6 + columns.length;
  return (
    <section className={cn('flex min-h-[24rem] min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-xs', className)} aria-labelledby="links-title">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 px-4 py-3">
        <h2 id="links-title" className="mr-auto whitespace-nowrap text-[15px] font-semibold">
          Danh sách link{' '}
          <span className="font-normal text-muted-foreground" data-testid="row-count">
            {table.state === 'ready' ? (filtered ? `${formatNumber(rows.length)} / ${formatNumber(total)}` : formatNumber(total)) : ''}
          </span>
        </h2>
        <div className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 sm:flex sm:w-auto">
          <NativeSelect className="min-w-0 sm:w-48" value={partner} onChange={event => setPartnerFilter(event.target.value)} aria-label="Lọc theo đối tác" disabled={!partners.length}>
            <option value="">Tất cả đối tác</option>
            {partners.map(name => <option key={name} value={name}>{name}</option>)}
          </NativeSelect>
          <div className="relative col-span-2 row-start-1 min-w-0 sm:w-64">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input type="search" className="pl-8 pr-8" placeholder="Tìm kênh, link, đối tác…" value={search} onChange={event => setSearch(event.target.value)} aria-label="Tìm trong danh sách link" />
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
      <div className="min-h-0 flex-1 overflow-auto border-t md:max-h-[calc(100dvh-14rem)]" data-testid="link-table-scroll">
        {table.state === 'loading' && <div className="space-y-2 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>}
        {table.state === 'error' && <p className="px-4 py-16 text-center text-sm text-destructive" role="alert">{table.text}</p>}
        {table.state === 'empty' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Chưa có tháng nào có link {platformConfig(platform).label}.</p>}
        {table.state === 'ready' && (
          <table className="w-full border-separate border-spacing-0 text-[13px]" data-testid="link-table">
            <thead>
              <tr>
                <th className={cn(th, 'pl-4')}>Ngày air</th>
                <th className={th}>Tên kênh</th>
                <th className={th}>Link</th>
                <th className={th}>Đối tác</th>
                {columns.map(column => <th key={column.key} className={cn(th, 'text-right')}>{column.label}</th>)}
                <th className={th}>Trạng thái quét</th>
                <th className={cn(th, 'pr-4')}>Cập nhật</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={colSpan} className="px-4 py-14 text-center text-sm text-muted-foreground">
                  {total ? 'Không có link nào khớp với bộ lọc.' : `Tháng này chưa có link ${platformConfig(platform).label}.`}
                </td></tr>
              )}
              {rows.slice(0, ROW_RENDER_LIMIT).map(row => <Row key={row.id} row={row} platform={platform} />)}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="font-semibold" data-testid="totals-row">
                  <td colSpan={4} className="sticky bottom-0 border-t bg-muted px-3 py-2.5 pl-4">Tổng{filtered ? ' (đang lọc)' : ''}</td>
                  {columns.map(column => (
                    <td key={column.key} className="sticky bottom-0 border-t bg-muted px-3 py-2.5 text-right tabular-nums" data-testid={`total-${column.key}`}>{formatResultNumber(totals[column.key])}</td>
                  ))}
                  <td colSpan={2} className="sticky bottom-0 border-t bg-muted px-3 py-2.5" />
                </tr>
              </tfoot>
            )}
          </table>
        )}
      </div>
      {rows.length > ROW_RENDER_LIMIT && (
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">Đang hiện {formatNumber(ROW_RENDER_LIMIT)} link đầu; dùng tìm kiếm hoặc lọc đối tác để thu hẹp. Tổng vẫn tính trên mọi link khớp.</p>
      )}
    </section>
  );
}
