import { useMemo, useState } from 'react';
import { Tooltip as TooltipPrimitive } from 'radix-ui';
import { DownloadIcon, RefreshCwIcon, SearchIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input, Skeleton } from '@/components/ui/misc';
import { Picker, type PickerOption } from '@/components/ui/picker';
import { formatNumber, formatResultNumber } from '@/lib/format';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import {
  columnTotals, filterRows, lastUpdateText, linkKind, metricColumns, scanStatusView, shortLink, shortUpdate, type LinkKind, type RdRow, type ScanStatusTone,
} from '@/lib/riviuData';
import { scanIsBusy, viewerScanText } from '@/lib/scan';
import { cn } from '@/lib/utils';
import { notify, openModal, useApp } from '@/state/app';
import { monthsOf, refreshView, selectThang } from '@/state/riviuData';

// ---- Report context: the month picker, its freshness and export ------------------------

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

/** The month picker doubles as the page title. */
export function MonthSelect() {
  const platform = useApp(state => state.activePlatform);
  const months = useApp(state => monthsOf(state));
  const thang = useApp(state => state.rd.thang[state.activePlatform]);
  const loaded = useApp(state => state.rd.months[state.activePlatform] !== null);
  const options = useMemo<PickerOption[]>(() => months.map(month => ({ value: month.thang, label: month.label, hint: `${formatNumber(month.rows)} link` })), [months]);
  return (
    <Picker
      value={thang}
      onChange={value => void selectThang(value)}
      options={options}
      placeholder={loaded ? 'Chưa có tháng nào' : 'Đang tải…'}
      disabled={!months.length}
      searchable={false}
      aria-label={`Tháng báo cáo ${platformConfig(platform).label}`}
      data-testid="month-select"
      triggerClassName="-ml-2 h-10 rounded-lg px-2 text-xl font-semibold tracking-tight hover:bg-muted sm:text-[22px]"
      contentClassName="w-64"
    />
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
    <section className="flex flex-wrap items-center gap-x-4 gap-y-1.5 py-3" aria-labelledby="report-title">
      <h1 id="report-title" className="sr-only">Báo cáo {platformConfig(platform).label} · {month?.label || thang || 'Chưa có dữ liệu'}</h1>
      <MonthSelect />
      {updated && <span className="text-sm text-muted-foreground" data-testid="last-update">{updated}</span>}
      <ScanStatePill />
      <Button className="ml-auto h-9" onClick={() => openModal('export')} disabled={!thang} data-testid="open-export">
        <DownloadIcon /> Xuất báo cáo
      </Button>
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

const KIND_TAGS: Record<Exclude<LinkKind, ''>, { label: string; className: string }> = {
  video: { label: 'Video', className: 'bg-sky-500/12 text-sky-700 dark:text-sky-300' },
  photo: { label: 'Ảnh', className: 'bg-violet-500/12 text-violet-700 dark:text-violet-300' },
  post: { label: 'Bài viết', className: 'bg-slate-500/12 text-slate-700 dark:text-slate-300' },
};

const tag = 'inline-flex shrink-0 items-center rounded px-1.5 py-px text-[11px] font-semibold leading-4';
const th = 'sticky top-0 z-10 truncate border-b bg-card px-2.5 py-2.5 text-left text-xs font-medium text-muted-foreground';
const td = 'border-b border-border/60 px-2.5 py-2 align-middle';

function Partners({ names }: { names: string[] }) {
  if (!names.length) return null;
  const chips = (
    <span className="flex min-w-0 gap-1" tabIndex={0} aria-label={`Đối tác: ${names.join(', ')}`}>
      <span className="min-w-0 truncate whitespace-nowrap rounded-md bg-muted px-1.5 py-0.5 text-xs text-foreground/80">{names[0]}</span>
      {names.length > 1 && <span className="shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">+{names.length - 1}</span>}
    </span>
  );
  return (
    <TooltipPrimitive.Root delayDuration={150}>
      <TooltipPrimitive.Trigger asChild>{chips}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content side="left" sideOffset={6} className="z-50 max-w-xs rounded-lg border bg-popover p-2.5 text-popover-foreground shadow-lg animate-in fade-in-0 zoom-in-95">
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">{names.length} đối tác</p>
          <ul className="flex flex-wrap gap-1">
            {names.map(name => <li key={name} className="rounded-md bg-muted px-1.5 py-0.5 text-xs">{name}</li>)}
          </ul>
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

function openLink(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer');
}

function copyLink(url: string) {
  if (!navigator.clipboard) {
    notify('Trình duyệt không cho sao chép; bấm chuột phải → Sao chép địa chỉ liên kết.', 'warn');
    return;
  }
  navigator.clipboard.writeText(url).then(() => notify('Đã sao chép link', 'success'), () => notify('Không sao chép được link', 'error'));
}

/** One click selects, a double click opens, Ctrl/⌘ + click or Ctrl/⌘ + C copies; right-click keeps the browser's own link menu. */
function LinkCell({ url, exclusive }: { url: string; exclusive: boolean }) {
  const { handle } = shortLink(url);
  const kind = linkKind(url);
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={`${url}\nBấm đúp để mở · Ctrl + C để sao chép`}
        data-testid="post-link"
        className="min-w-0 select-none truncate rounded-sm font-medium text-primary outline-none hover:underline focus:bg-primary/10 focus:ring-2 focus:ring-primary/40"
        onClick={event => {
          event.preventDefault();
          if (event.ctrlKey || event.metaKey) copyLink(url);
          else event.currentTarget.focus();
        }}
        onDoubleClick={() => openLink(url)}
        onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
            event.preventDefault();
            copyLink(url);
          } else if (event.key === 'Enter') {
            event.preventDefault();
            openLink(url);
          }
        }}
      >
        {handle}
      </a>
      {kind && <span className={cn(tag, KIND_TAGS[kind].className)}>{KIND_TAGS[kind].label}</span>}
      {exclusive && <span className={cn(tag, 'bg-primary/12 text-primary')} data-testid="exclusive-tag">Độc quyền</span>}
    </span>
  );
}

function Row({ row, platform, showNguoiAir }: { row: RdRow; platform: PlatformKey; showNguoiAir: boolean }) {
  const status = scanStatusView(row);
  return (
    <tr className="hover:bg-muted/40" data-testid="link-row">
      <td className={cn(td, 'whitespace-nowrap pl-4 tabular-nums text-muted-foreground')}>{row.ngayAir}</td>
      {showNguoiAir && <td className={cn(td, 'truncate')} title={row.nguoiAir}>{row.nguoiAir}</td>}
      <td className={cn(td, 'truncate')} title={row.channel}>{row.channel}</td>
      <td className={td}><LinkCell url={row.link} exclusive={row.partners.length === 1} /></td>
      {metricColumns(platform).map(column => (
        <td key={column.key} className={cn(td, 'text-right font-medium tabular-nums')} data-metric={column.key}>{formatResultNumber(row[column.key])}</td>
      ))}
      <td className={td}><Partners names={row.partners} /></td>
      <td className={td}>
        <span className={cn('inline-flex max-w-full truncate whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold', STATUS_TONE[status.tone])} title={status.title}>{status.label}</span>
      </td>
      <td className={cn(td, 'whitespace-nowrap pr-4 text-xs tabular-nums text-muted-foreground')} title={row.updatedAt}>{shortUpdate(row.updatedAt)}</td>
    </tr>
  );
}

const ROW_RENDER_LIMIT = 3000;

export function LinkTablePanel({ className }: { className?: string }) {
  const table = useApp(state => state.rd.table);
  const platform = useApp(state => state.activePlatform);
  const [search, setSearch] = useState('');
  const [partnerFilter, setPartnerFilter] = useState('');
  const allRows = useMemo(() => (table.state === 'ready' ? table.rows : []), [table]);
  const partnerOptions = useMemo<PickerOption[]>(() => {
    if (table.state !== 'ready') return [];
    const counts = new Map<string, number>();
    for (const row of table.rows) for (const name of row.partners) counts.set(name, (counts.get(name) || 0) + 1);
    const names = [...table.partners].sort((a, b) => a.localeCompare(b, 'vi'));
    return [
      { value: '', label: 'Tất cả đối tác', hint: formatNumber(table.rows.length) },
      ...names.map(name => ({ value: name, label: name, hint: formatNumber(counts.get(name) || 0) })),
    ];
  }, [table]);
  const partner = partnerOptions.some(option => option.value && option.value === partnerFilter) ? partnerFilter : '';
  const rows = useMemo(() => filterRows(allRows, { search, partner }), [allRows, search, partner]);
  const totals = useMemo(() => columnTotals(rows, platform), [rows, platform]);
  // Người air shows once Riviu Data stores it (migration 0013), so an empty column never takes space.
  const showNguoiAir = useMemo(() => allRows.some(row => row.nguoiAir), [allRows]);
  const total = allRows.length;
  const filtered = Boolean(search.trim() || partner);
  const columns = metricColumns(platform);
  const leading = showNguoiAir ? 4 : 3;
  const colSpan = leading + columns.length + 3;
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
          <Picker
            className="w-full sm:w-56"
            value={partner}
            onChange={setPartnerFilter}
            options={partnerOptions}
            placeholder="Tất cả đối tác"
            searchable
            searchPlaceholder="Tìm đối tác…"
            aria-label="Lọc theo đối tác"
            disabled={partnerOptions.length <= 1}
            align="end"
            contentClassName="w-72"
          />
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
      <div className="min-h-0 flex-1 overflow-auto border-t md:max-h-[calc(100dvh-12rem)]" data-testid="link-table-scroll">
        {table.state === 'loading' && <div className="space-y-2 p-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>}
        {table.state === 'error' && <p className="px-4 py-16 text-center text-sm text-destructive" role="alert">{table.text}</p>}
        {table.state === 'empty' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Chưa có tháng nào có link {platformConfig(platform).label}.</p>}
        {table.state === 'ready' && (
          // Fixed layout: every column fits the page width (no sideways scrolling from about 1100px up).
          <table className="w-full min-w-[1080px] table-fixed border-separate border-spacing-0 text-[13px]" data-testid="link-table">
            <colgroup>
              <col className="w-[100px]" />
              {showNguoiAir && <col className="w-[96px]" />}
              <col className="w-[14%]" />
              <col className="w-[21%]" />
              {columns.map(column => <col key={column.key} className={column.key === 'views' ? 'w-[84px]' : 'w-[74px]'} />)}
              <col />
              <col className="w-[104px]" />
              <col className="w-[96px]" />
            </colgroup>
            <thead>
              <tr>
                <th className={cn(th, 'pl-4')}>Ngày đăng</th>
                {showNguoiAir && <th className={th}>Người đăng</th>}
                <th className={th}>Tên kênh</th>
                <th className={th}>Link</th>
                {columns.map(column => <th key={column.key} className={cn(th, 'text-right')}>{column.label}</th>)}
                <th className={th}>Đối tác</th>
                <th className={th}>Trạng thái</th>
                <th className={cn(th, 'pr-4')}>Cập nhật</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr><td colSpan={colSpan} className="px-4 py-14 text-center text-sm text-muted-foreground">
                  {total ? 'Không có link nào khớp với bộ lọc.' : `Tháng này chưa có link ${platformConfig(platform).label}.`}
                </td></tr>
              )}
              {rows.slice(0, ROW_RENDER_LIMIT).map(row => <Row key={row.id} row={row} platform={platform} showNguoiAir={showNguoiAir} />)}
            </tbody>
            {rows.length > 0 && (
              <tfoot>
                <tr className="font-semibold" data-testid="totals-row">
                  <td colSpan={leading} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5 pl-4">Tổng{filtered ? ' (đang lọc)' : ''}</td>
                  {columns.map(column => (
                    <td key={column.key} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5 text-right tabular-nums" data-testid={`total-${column.key}`}>{formatResultNumber(totals[column.key])}</td>
                  ))}
                  <td colSpan={3} className="sticky bottom-0 border-t bg-muted px-2.5 py-2.5" />
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
