import { useMemo, useRef, useState, useEffect } from 'react';
import {
  ArrowUpIcon, CheckCircle2Icon, CopyIcon, DownloadIcon, EyeOffIcon, FileWarningIcon, RadioIcon, RefreshCwIcon, Rows3Icon, TerminalIcon, Trash2Icon, CopyPlusIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, Progress, Skeleton } from '@/components/ui/misc';
import { EmptyRow, tableClass, tdClass, thClass, useIsAdmin } from '@/components/common';
import { formatNumber, formatResultNumber, normalizeVietnameseKey, sumKnown } from '@/lib/format';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import {
  failedLinksForKind, groupDuplicateLocations, isNoStatsStatus, liveStatusLabel, progressPercent, progressPlatform, progressResultCounts, progressStatusView,
  progressSummaryText, scanIsBusy, type FailedItem, type LiveRow, type LogDetails,
} from '@/lib/scan';
import { visibleSheetTabs } from '@/lib/sheets';
import { cn } from '@/lib/utils';
import {
  activeSource, clearDuplicateLinks, clearFailedLinks, clearLogs, copyDuplicateLinks, copyFailedLinks, copyLogs, downloadCurrentWorkbook, exportLogs, loadPreview,
  setWorkspaceTab, switchSheet, useApp, type AppState, type LogEntry, type WorkspaceTab,
} from '@/state/app';
import type { PreviewRow } from '@/lib/links';

const VIEW_META: Record<WorkspaceTab, { title: string; subtitle: string }> = {
  sheet: { title: 'Dữ liệu sheet hiện tại', subtitle: 'Xem và chuyển sheet trong cùng một vùng làm việc' },
  live: { title: 'Kết quả đang quét', subtitle: 'Kết quả mới nhất nằm ở đầu danh sách' },
  success: { title: 'Đọc số liệu thành công', subtitle: 'Chỉ hiển thị các link đã đọc được số liệu' },
  nostats: { title: 'Không đọc được số liệu', subtitle: 'Link hoạt động nhưng nền tảng không trả số liệu' },
  errors: { title: 'Lỗi quét cần kiểm tra', subtitle: 'Lỗi kết nối, link không hợp lệ hoặc lỗi xử lý' },
  duplicates: { title: 'Link bị trùng', subtitle: 'Hiển thị đầy đủ sheet và số dòng của từng link' },
  logs: { title: 'Nhật ký hệ thống', subtitle: 'Theo dõi chi tiết quá trình quét và cập nhật dữ liệu' },
};

function useCounts() {
  const scan = useApp(state => state.scan);
  const active = useApp(state => state.activePlatform);
  const sheetLinkCount = useApp(state => state.sheetLinkCount);
  const logCount = useApp(state => state.logs.length);
  return useMemo(() => {
    const platform = scan.progress ? progressPlatform(scan.progress, scan, active) : active;
    const counts = scan.progress ? progressResultCounts(scan.progress, scan.rows, platform) : { success: 0, hidden: 0, error: 0 };
    const failedNoStats = failedLinksForKind(scan.failed, 'nostats').length;
    const failedErrors = failedLinksForKind(scan.failed, 'errors').length;
    return {
      sheet: scan.progress && scanIsBusy(scan.phase) ? Number(scan.progress.total || 0) : sheetLinkCount,
      live: Number(scan.progress?.processed || 0),
      success: counts.success,
      // These two badges count the rows their tab lists (cleared with the list).
      nostats: failedNoStats,
      errors: failedErrors,
      duplicates: scan.duplicates.length,
      logs: logCount,
    };
  }, [scan, active, sheetLinkCount, logCount]);
}

const TABS: Array<{ key: WorkspaceTab; label: string; icon: React.ReactNode; tone?: string }> = [
  { key: 'sheet', label: 'Dữ liệu sheet', icon: <Rows3Icon /> },
  { key: 'live', label: 'Live', icon: <RadioIcon /> },
  { key: 'success', label: 'Thành công', icon: <CheckCircle2Icon />, tone: 'text-success' },
  { key: 'nostats', label: 'Không số liệu', icon: <EyeOffIcon />, tone: 'text-warning' },
  { key: 'errors', label: 'Lỗi quét', icon: <FileWarningIcon />, tone: 'text-destructive' },
  { key: 'duplicates', label: 'Link trùng', icon: <CopyPlusIcon /> },
  { key: 'logs', label: 'Nhật ký', icon: <TerminalIcon /> },
];

export function WorkspaceTabs() {
  const tab = useApp(state => state.workspaceTab);
  const counts = useCounts();
  return (
    <nav className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1" role="tablist" aria-label="Các vùng dữ liệu">
      {TABS.map(item => {
        const selected = item.key === tab;
        return (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => setWorkspaceTab(item.key)}
            className={cn(
              'flex h-9 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium transition [&_svg]:size-4',
              selected ? 'bg-card text-foreground shadow-sm ring-1 ring-border' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            <span className={cn(item.tone)}>{item.icon}</span>
            {item.label}
            <span data-testid={`count-${item.key}`} className={cn('rounded-full px-1.5 py-px text-[11px] tabular-nums', selected ? 'bg-accent text-accent-foreground' : 'bg-muted text-muted-foreground')}>
              {formatNumber(counts[item.key])}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export function ProgressSection() {
  const scan = useApp(state => state.scan);
  const active = useApp(state => state.activePlatform);
  const progress = scan.progress;
  const platform = progress ? progressPlatform(progress, scan, active) : active;
  const counts = progress ? progressResultCounts(progress, scan.rows, platform) : { success: 0, hidden: 0, error: 0 };
  const [statusText, tone] = progressStatusView(scan.phase, counts, platform, progress?.stopReason);
  return (
    <div className="flex flex-col gap-1.5">
      <Progress value={progressPercent(progress)} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span aria-live="polite" data-testid="progress-text">{progress ? progressSummaryText(progress) : 'Sẵn sàng chờ lệnh...'}</span>
        {statusText && (
          <Badge variant={tone === 'success' ? 'success' : tone === 'warn' ? 'warning' : 'secondary'} data-testid="progress-status">{statusText}</Badge>
        )}
      </div>
    </div>
  );
}

// ---- Sheet panel ----------------------------------------------------------------------

const METRIC_HEADERS = new Set(['luot xem', 'tim', 'binh luan', 'luot luu', 'repost', 'chia se', 'tong luot xem', 'tong tim', 'tong binh luan', 'tong luot luu', 'tong repost', 'tong chia se']);

function previewCell(column: string, value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' && METRIC_HEADERS.has(normalizeVietnameseKey(column))) return formatResultNumber(value);
  return String(value);
}

function linkColorClass(row: { videoLink?: boolean; singlePartner?: boolean; _videoLink?: boolean; _singlePartner?: boolean }) {
  if (row.videoLink || row._videoLink) return 'text-video-link';
  if (row.singlePartner || row._singlePartner) return 'text-single-partner-link';
  return 'text-primary';
}

function PreviewTable({ columns, rows, platform }: { columns: string[]; rows: PreviewRow[]; platform: PlatformKey }) {
  return (
    <table className={tableClass}>
      <thead><tr>{columns.map(column => <th key={column} className={thClass}>{column}</th>)}</tr></thead>
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={Math.max(columns.length, 1)}>Sheet này chưa có link {platformConfig(platform).label}.</EmptyRow>}
        {rows.map((row, index) => {
          const isTotal = Object.values(row).some(value => normalizeVietnameseKey(value) === 'tong');
          return (
            <tr key={index} className={cn('hover:bg-muted/40', row._videoLink && 'bg-video-link/5', row._singlePartner && 'bg-single-partner-link/5', isTotal && 'bg-muted/60 font-semibold')}>
              {columns.map(column => {
                const text = previewCell(column, row[column]);
                const numeric = METRIC_HEADERS.has(normalizeVietnameseKey(column));
                return (
                  <td key={column} className={cn(tdClass, 'max-w-[22rem] truncate', numeric && 'text-right tabular-nums')} title={text}>
                    {text.startsWith('http') ? (
                      <a href={text} target="_blank" rel="noopener noreferrer" className={cn('hover:underline', linkColorClass(row))}>{text}</a>
                    ) : text}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function summaryCell(column: string, value: unknown): string {
  const key = normalizeVietnameseKey(column);
  if (key === 'doi tac' || key === 'cap nhat lan cuoi') return String(value ?? '');
  if (key === 'stt') return value === '' || value === null || value === undefined ? '' : formatNumber(value);
  if (key === 'tong link') return formatNumber(value);
  return formatResultNumber(value);
}

const SUMMARY_TEXT_COLUMNS = new Set(['doi tac', 'cap nhat lan cuoi', 'stt']);

export function SummaryTable({ columns, rows }: { columns: string[]; rows: Array<Record<string, unknown>> }) {
  return (
    <table className={tableClass} data-testid="summary-table">
      <thead><tr>{columns.map(column => <th key={column} className={thClass}>{column}</th>)}</tr></thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={index} className="hover:bg-muted/40">
            {columns.map(column => {
              const key = normalizeVietnameseKey(column);
              const text = summaryCell(column, row[column] ?? '');
              return (
                <td key={column} className={cn(tdClass, key === 'doi tac' ? 'max-w-[18rem] truncate font-medium' : '', !SUMMARY_TEXT_COLUMNS.has(key) && key !== 'doi tac' && 'text-right tabular-nums', key === 'tong link' && 'font-semibold')} title={text}>
                  {text}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="bg-muted/60 font-semibold">
          {columns.map((column, index) => {
            const key = normalizeVietnameseKey(column);
            if (key === 'doi tac') return <td key={column} className={tdClass}>TỔNG</td>;
            if (SUMMARY_TEXT_COLUMNS.has(key)) return <td key={column} className={tdClass}>{index === 0 && key !== 'stt' ? 'TỔNG' : ''}</td>;
            // Known values only; blank when nothing in the column is known.
            const total = sumKnown(rows.map(row => row[column]));
            return <td key={column} className={cn(tdClass, 'text-right tabular-nums')} data-testid={`summary-total-${key}`}>{formatResultNumber(total)}</td>;
          })}
        </tr>
      </tfoot>
    </table>
  );
}

function SheetTabs() {
  const source = useApp(state => activeSource(state));
  const platform = useApp(state => state.activePlatform);
  const disabled = useApp(state => state.sourceBusy || scanIsBusy(state.scan.phase));
  const hidden = new Set(source.hiddenSheets);
  const tabs = visibleSheetTabs(source.sheets, platform).filter(sheet => !hidden.has(sheet));
  if (tabs.length <= 1) return null;
  return (
    <div className="flex gap-1 overflow-x-auto border-b px-3 pt-2" role="tablist" aria-label="Sheet">
      {tabs.map(sheet => (
        <button
          key={sheet}
          type="button"
          role="tab"
          aria-selected={sheet === source.displaySheet}
          disabled={disabled}
          onClick={() => void switchSheet(sheet)}
          className={cn(
            '-mb-px shrink-0 whitespace-nowrap rounded-t-md border border-transparent px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed',
            sheet === source.displaySheet ? 'border-border border-b-card bg-card text-primary' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          {sheet}
        </button>
      ))}
    </div>
  );
}

function SheetPanel() {
  const preview = useApp(state => state.preview);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SheetTabs />
      <div className="min-h-0 flex-1 overflow-auto">
        {preview.kind === 'loading' && (
          <div className="space-y-2 p-4">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-7 w-full" />)}</div>
        )}
        {preview.kind === 'empty' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Chưa chọn nguồn cho nền tảng này.</p>}
        {preview.kind === 'message' && <p className={cn('px-4 py-16 text-center text-sm', preview.muted ? 'text-muted-foreground' : '')}>{preview.text}</p>}
        {preview.kind === 'table' && <PreviewTable columns={preview.columns} rows={preview.rows} platform={preview.platform} />}
        {preview.kind === 'summary' && preview.summary.state === 'loading' && <p className="px-4 py-16 text-center text-sm text-muted-foreground">Đang tải tổng kết đối tác...</p>}
        {preview.kind === 'summary' && preview.summary.state === 'error' && <p className="px-4 py-16 text-center text-sm text-destructive">{preview.summary.text}</p>}
        {preview.kind === 'summary' && preview.summary.state === 'ready' && (
          <div>
            <div className="flex items-center gap-2 px-4 py-3">
              <h3 className="font-semibold">{preview.summary.title}</h3>
              {preview.summary.partners > 0 && <Badge variant="brand">{formatNumber(preview.summary.partners)} đối tác</Badge>}
            </div>
            {preview.summary.rows.length === 0
              ? <p className="px-4 pb-12 pt-6 text-center text-sm text-muted-foreground">Chưa có đối tác nào. Quét sheet dữ liệu để tạo tổng kết.</p>
              : <SummaryTable columns={preview.summary.columns} rows={preview.summary.rows} />}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- Live panel -----------------------------------------------------------------------

const LIVE_RENDER_LIMIT = 2000;
const LIVE_PINNED_SCROLL_PX = 32;

function LivePanel({ onlySuccess }: { onlySuccess: boolean }) {
  const rows = useApp(state => state.scan.rows);
  const runPlatform = useApp(state => state.scan.runContext?.platform);
  const active = useApp(state => state.activePlatform);
  const platform = (runPlatform === 'threads' || runPlatform === 'tiktok' ? runPlatform : active) as PlatformKey;
  const config = platformConfig(platform);
  const visible = useMemo(() => (onlySuccess ? rows.filter(row => row.kind === 'success') : rows).slice(0, LIVE_RENDER_LIMIT), [rows, onlySuccess]);
  const wrap = useRef<HTMLDivElement>(null);
  const [pending, setPending] = useState(0);
  const lastTop = useRef<number | undefined>(undefined);
  const newest = visible[0]?.seq;
  useEffect(() => {
    // Scrolled down, the reader keeps their place as new rows arrive on top.
    if (newest === undefined || lastTop.current === newest) return;
    const element = wrap.current;
    if (lastTop.current !== undefined && element && element.scrollTop > LIVE_PINNED_SCROLL_PX) setPending(count => count + 1);
    lastTop.current = newest;
  }, [newest]);
  const showNewest = () => {
    wrap.current?.scrollTo({ top: 0, behavior: 'smooth' });
    setPending(0);
  };
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={wrap} className="min-h-0 flex-1 overflow-auto" onScroll={event => { if (event.currentTarget.scrollTop <= LIVE_PINNED_SCROLL_PX) setPending(0); }}>
        <table className={tableClass}>
          <thead>
            <tr>
              <th className={thClass}>ID</th><th className={thClass}>Sheet</th><th className={thClass}>Tên kênh</th>
              <th className={thClass}>{config.liveLinkHeader}</th>
              <th className={cn(thClass, 'text-right')}>Lượt xem</th><th className={cn(thClass, 'text-right')}>Tim</th>
              <th className={cn(thClass, 'text-right')}>Bình luận</th><th className={cn(thClass, 'text-right')}>{config.savedMetric.column}</th>
              <th className={cn(thClass, 'text-right')}>Chia sẻ</th><th className={thClass}>Status</th>
            </tr>
          </thead>
          <tbody data-testid="live-rows">
            {visible.length === 0 && <EmptyRow colSpan={10}>Chưa có kết quả mới</EmptyRow>}
            {visible.map(row => <LiveResultRow key={row.seq} row={row} />)}
          </tbody>
        </table>
      </div>
      {pending > 0 && (
        <Button size="sm" className="absolute left-1/2 top-12 -translate-x-1/2 shadow-lg" onClick={showNewest}>
          <ArrowUpIcon /> Có {pending} kết quả mới
        </Button>
      )}
    </div>
  );
}

function LiveResultRow({ row }: { row: LiveRow }) {
  const missing = Array.isArray(row.missingMetrics) && row.missingMetrics.length ? ` • Nguồn chưa trả: ${row.missingMetrics.join(', ')}` : '';
  const tone = row.kind === 'success' ? 'text-success' : row.kind === 'nostats' ? 'text-warning' : 'text-destructive';
  const metric = (value: unknown) => <td className={cn(tdClass, 'text-right font-semibold tabular-nums')}>{formatResultNumber(value)}</td>;
  return (
    <tr className={cn('hover:bg-muted/40', row.videoLink && 'bg-video-link/5', row.singlePartner && 'bg-single-partner-link/5')} data-result={row.kind}>
      <td className={cn(tdClass, 'tabular-nums')}>{String(row.id ?? '')}</td>
      <td className={cn(tdClass, 'whitespace-nowrap')}>{row.sheetName || ''}</td>
      <td className={cn(tdClass, 'max-w-40 truncate')} title={row.channelName || ''}>{row.channelName || ''}</td>
      <td className={cn(tdClass, 'max-w-[20rem] truncate')} title={row.url}>
        <a href={row.url} target="_blank" rel="noopener noreferrer" className={cn('hover:underline', row.videoLink || row.singlePartner ? linkColorClass(row) : '')}>{row.url}</a>
      </td>
      {metric(row.views)}{metric(row.likes)}{metric(row.comments)}{metric(row.saves)}{metric(row.shares)}
      <td className={tdClass}><span className={cn('text-xs font-bold', tone)} title={`${row.status || ''}${missing}`}>{liveStatusLabel(row)}</span></td>
    </tr>
  );
}

// ---- Failure, duplicate and log panels ------------------------------------------------

function FailurePanel({ kind }: { kind: 'nostats' | 'errors' }) {
  const failed = useApp(state => state.scan.failed);
  const active = useApp(state => state.activePlatform);
  const items = useMemo(() => failedLinksForKind(failed, kind), [failed, kind]);
  const config = platformConfig(active);
  const reason = (item: FailedItem) => (isNoStatsStatus(item.status) ? config.noStatsReason(item) : item.status || 'Lỗi không xác định');
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className={tableClass}>
        <thead>
          <tr>
            <th className={thClass}>ID</th><th className={thClass}>Sheet</th>
            <th className={thClass}>{kind === 'nostats' ? 'Link không số liệu' : 'Link lỗi'}</th>
            <th className={thClass}>Lý do</th><th className={thClass}>Luồng</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && <EmptyRow colSpan={5}>{kind === 'nostats' ? 'Chưa có link không đọc được số liệu' : 'Chưa có link lỗi'}</EmptyRow>}
          {items.map((item, index) => (
            <tr key={`${item.id}-${index}`} className="hover:bg-muted/40">
              <td className={cn(tdClass, 'tabular-nums')}>{String(item.id ?? '')}</td>
              <td className={cn(tdClass, 'whitespace-nowrap')}>{item.sheetName || ''}</td>
              <td className={cn(tdClass, 'max-w-[22rem] truncate')} title={item.url}><a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{item.url}</a></td>
              <td className={cn(tdClass, kind === 'nostats' ? 'text-warning' : 'text-destructive')} title={item.status || ''}>
                {reason(item)}
                {kind === 'nostats' && <Badge variant="warning" className="ml-2">{config.noStatsTag}</Badge>}
              </td>
              <td className={cn(tdClass, 'whitespace-nowrap')}>{item.worker ? `Luồng ${item.worker}` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DuplicatesPanel() {
  const items = useApp(state => state.scan.duplicates);
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className={tableClass}>
        <thead><tr><th className={thClass}>ID</th><th className={thClass}>Link bị trùng</th><th className={thClass}>Số lần</th><th className={thClass}>Sheet / số dòng</th></tr></thead>
        <tbody>
          {items.length === 0 && <EmptyRow colSpan={4}>Chưa có link trùng</EmptyRow>}
          {items.map((item, index) => (
            <tr key={`${item.url}-${index}`} className="hover:bg-muted/40">
              <td className={cn(tdClass, 'tabular-nums')}>{String(item.id || index + 1)}</td>
              <td className={cn(tdClass, 'max-w-[24rem] truncate')} title={item.url}><a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{item.url}</a></td>
              <td className={cn(tdClass, 'font-semibold tabular-nums')}>{(item.locations || []).length}</td>
              <td className={tdClass}>
                {Array.from(groupDuplicateLocations(item.locations).entries()).map(([sheet, rowsOf]) => (
                  <div key={sheet} className="flex flex-wrap gap-1.5"><Badge variant="secondary">{sheet}</Badge><span>dòng {rowsOf.map(row => String(row ?? '')).join(', ')}</span></div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const LOG_TONE: Record<string, string> = { ERROR: 'text-red-400', WARN: 'text-amber-300', OK: 'text-emerald-300' };

function MetricChip({ label, value }: { label: string; value: unknown }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-white/10 px-1.5 py-px">
      <span className="text-white/60">{label}</span><span className="font-semibold text-white">{formatResultNumber(value)}</span>
    </span>
  );
}

function RichLog({ details }: { details: LogDetails }) {
  const progress = <span className="rounded bg-white/10 px-1.5 text-white/80">[{details.processed}/{details.total}]</span>;
  const tail = (
    <>
      <span className="text-white/50">Luồng {String(details.worker || '?')}</span>
      <span className="text-white/50">{String(details.elapsed)}s</span>
      {details.rows && <span className="text-white/50" title="Dòng Excel">dòng {details.rows}</span>}
      {details.url && <a className="basis-full truncate text-sky-300 hover:underline" href={details.url} target="_blank" rel="noopener noreferrer">{details.url}</a>}
    </>
  );
  if (details.kind === 'scrape_ok') {
    const m = details.metrics || {};
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        {progress}<span className="rounded bg-emerald-500/20 px-1.5 font-semibold text-emerald-300">OK</span>
        <span className="text-white">{details.channel || '—'}</span>
        <MetricChip label="Lượt xem" value={m.views} /><MetricChip label="Tim" value={m.likes} /><MetricChip label="Bình luận" value={m.comments} />
        <MetricChip label="Lưu" value={m.saves} /><MetricChip label="Chia sẻ" value={m.shares} />
        {tail}
      </span>
    );
  }
  if (details.kind === 'scrape_hidden') {
    return <span className="inline-flex flex-wrap items-center gap-1.5">{progress}<span className="rounded bg-amber-500/20 px-1.5 text-amber-300">Ẩn số liệu</span><span className="text-white/70">TikTok không trả lượt xem cho post này</span>{tail}</span>;
  }
  if (details.kind === 'scrape_error') {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        {progress}<span className="rounded bg-red-500/20 px-1.5 text-red-300">Lỗi</span><span className="text-red-300">{details.status || 'Lỗi không xác định'}</span>
        {details.attempts ? <span className="text-white/50">Thử {details.attempts} lần</span> : null}{tail}
      </span>
    );
  }
  return null;
}

function LogLine({ line }: { line: LogEntry }) {
  const rich = line.details?.kind ? <RichLog details={line.details} /> : null;
  return (
    <div className="flex gap-2 py-0.5">
      <span className="shrink-0 text-white/40">[{line.time}]</span>
      {rich || <span className={cn('whitespace-pre-wrap break-words', LOG_TONE[line.level] || 'text-white/85')}>[{line.level}] {line.message}</span>}
    </div>
  );
}

function LogsPanel() {
  const logs = useApp(state => state.logs);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = box.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [logs.length]);
  return (
    <div ref={box} className="min-h-0 flex-1 overflow-auto bg-[#0f1115] p-3 font-mono text-[12px] leading-relaxed" data-testid="logs">
      {logs.map(line => <LogLine key={line.id} line={line} />)}
    </div>
  );
}

// ---- Panel frame ----------------------------------------------------------------------

function PanelActions({ tab }: { tab: WorkspaceTab }) {
  const admin = useIsAdmin();
  const hasFile = useApp(state => Boolean(activeSource(state).fileId));
  if (tab === 'sheet') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void loadPreview()} title="Làm mới dữ liệu"><RefreshCwIcon /><span className="hidden sm:inline">Làm mới</span></Button>
        {admin && <Button variant="outline" size="sm" onClick={downloadCurrentWorkbook} disabled={!hasFile} title="Tải sheet"><DownloadIcon /><span className="hidden sm:inline">Tải sheet</span></Button>}
      </>
    );
  }
  if (tab === 'nostats' || tab === 'errors') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyFailedLinks(tab)}><CopyIcon /><span className="hidden sm:inline">Copy link</span></Button>
        <Button variant="outline" size="sm" onClick={() => clearFailedLinks(tab)}><Trash2Icon /><span className="hidden sm:inline">Xóa</span></Button>
      </>
    );
  }
  if (tab === 'duplicates') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyDuplicateLinks()}><CopyIcon /><span className="hidden sm:inline">Copy link</span></Button>
        <Button variant="outline" size="sm" onClick={clearDuplicateLinks}><Trash2Icon /><span className="hidden sm:inline">Xóa</span></Button>
      </>
    );
  }
  if (tab === 'logs') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyLogs()}><CopyIcon /><span className="hidden sm:inline">Copy</span></Button>
        <Button variant="outline" size="sm" onClick={exportLogs}><DownloadIcon /><span className="hidden sm:inline">Xuất</span></Button>
        <Button variant="outline" size="sm" onClick={clearLogs}><Trash2Icon /><span className="hidden sm:inline">Xóa</span></Button>
      </>
    );
  }
  return null;
}

function failureNote(state: AppState): string {
  const errors = failedLinksForKind(state.scan.failed, 'errors').length;
  const noStats = failedLinksForKind(state.scan.failed, 'nostats').length;
  const parts = [];
  if (errors > 0) parts.push(`${errors} lỗi quét`);
  if (noStats > 0) parts.push(`${noStats} không đọc được số liệu`);
  return parts.length ? `(${parts.join(' • ')})` : '';
}

export function WorkspacePanel() {
  const tab = useApp(state => state.workspaceTab);
  const note = useApp(state => (state.workspaceTab === 'duplicates'
    ? (state.scan.duplicateRowCount > 0 ? `(${state.scan.duplicateRowCount} dòng được gộp)` : '')
    : state.workspaceTab === 'nostats' || state.workspaceTab === 'errors' ? failureNote(state) : ''));
  const meta = VIEW_META[tab];
  return (
    <section className="flex min-h-[28rem] flex-1 flex-col overflow-hidden rounded-xl border bg-card shadow-xs" aria-label={meta.title}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold leading-tight">{meta.title}</h2>
          <p className="text-xs text-muted-foreground">{meta.subtitle} {note && <span className="font-medium text-warning">{note}</span>}</p>
        </div>
        <div className="flex flex-wrap gap-1.5"><PanelActions tab={tab} /></div>
      </div>
      {tab === 'sheet' && <SheetPanel />}
      {(tab === 'live' || tab === 'success') && <LivePanel onlySuccess={tab === 'success'} />}
      {(tab === 'nostats' || tab === 'errors') && <FailurePanel kind={tab} />}
      {tab === 'duplicates' && <DuplicatesPanel />}
      {tab === 'logs' && <LogsPanel />}
    </section>
  );
}
