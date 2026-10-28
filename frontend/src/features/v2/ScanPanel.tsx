import { useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import {
  CircleStopIcon, CookieIcon, CopyIcon, DownloadIcon, EyeOffIcon, FileWarningIcon, GaugeIcon, Loader2Icon, NetworkIcon, PlayIcon, RadioIcon,
  Rows3Icon, Settings2Icon, SkipForwardIcon, SlidersHorizontalIcon, TerminalIcon, Trash2Icon, CopyPlusIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, NativeSelect, Progress, Switch, Tip } from '@/components/ui/misc';
import { formatNumber } from '@/lib/format';
import { WORKER_OPTIONS, platformConfig, type PlatformKey, type ScrapeMode } from '@/lib/platforms';
import {
  failedLinksForKind, progressLineText, progressPercent, progressPlatform, progressResultCounts, progressStatusView, retryWaitText, scanIsBusy,
} from '@/lib/scan';
import { cn } from '@/lib/utils';
import {
  cancelScraping, clearDuplicateLinks, clearFailedLinks, clearLogs, copyDuplicateLinks, copyFailedLinks, copyLogs, exportLogs, openModal, setWorkspaceTab,
  skipRetryWait, startBlockedReason, uiIsBusy, updateScanSettings, useApp, type CookieKind, type WorkspaceTab,
} from '@/state/app';
import { monthsOf, startRdScan } from '@/state/riviuData';
import { DuplicatesPanel, FailurePanel, LivePanel, LogsPanel } from '@/features/Workspace';
import { LinkTablePanel } from './LinkTable';

const COOKIE_STATE_TONE: Record<string, string> = {
  valid: 'bg-success', expired: 'bg-warning', invalid: 'bg-destructive', checkpoint: 'bg-warning', storage_unavailable: 'bg-destructive',
};
const COOKIE_LABELS: Record<string, string> = {
  none: 'Chưa import', unchecked: 'Chưa kiểm tra', valid: 'Hợp lệ', expired: 'Hết hạn', invalid: 'Không hợp lệ', checkpoint: 'Cần xác minh', unknown: 'Chưa xác định', storage_unavailable: 'Kho bảo mật không khả dụng',
};

function FieldLabel({ htmlFor, icon, children }: { htmlFor?: string; icon: ReactNode; children: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground [&_svg]:size-3.5">
      {icon}{children}
    </label>
  );
}

function ToggleField({ id, label, icon, checked, onChange, disabled, switchDisabled, onConfigure, dotClass, title }: {
  id: string; label: string; icon: ReactNode; checked: boolean; onChange: (value: boolean) => void; disabled: boolean; switchDisabled: boolean;
  onConfigure: () => void; dotClass: string; title: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <FieldLabel htmlFor={id} icon={icon}>{label}</FieldLabel>
      <div className={cn('flex h-9 items-center gap-2 rounded-md border bg-card pl-2.5 pr-1 shadow-xs', checked && 'border-primary/50 bg-accent/40')}>
        <Tip content={title}>
          <span className="flex items-center gap-2">
            <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled || switchDisabled} aria-label={label} />
            <span className={cn('size-2 rounded-full bg-muted-foreground/40', dotClass)} aria-hidden="true" />
          </span>
        </Tip>
        <Button variant="ghost" size="sm" className="ml-auto h-7 px-2 text-xs" onClick={onConfigure} aria-label={`Cấu hình ${label.toLowerCase()}`}>
          <SlidersHorizontalIcon /> Cấu hình
        </Button>
      </div>
    </div>
  );
}

function CookieField({ platform }: { platform: PlatformKey }) {
  const id = useId();
  const kind: CookieKind = platform;
  const status = useApp(state => state.cookies[kind]);
  const saved = useApp(state => state.scanSettings[platform].useSession);
  const busy = useApp(state => uiIsBusy(state));
  const label = `Cookie ${platformConfig(platform).label}`;
  return (
    <ToggleField
      id={id}
      label={label}
      icon={<CookieIcon />}
      // A saved "on" with no cookie stored shows off and comes back once a cookie is imported.
      checked={saved && status.configured}
      onChange={value => void updateScanSettings(platform, { useSession: value })}
      disabled={busy}
      switchDisabled={!status.configured}
      onConfigure={() => openModal(kind === 'threads' ? 'threadsCookie' : 'tiktokCookie')}
      dotClass={COOKIE_STATE_TONE[status.state] || ''}
      title={`${COOKIE_LABELS[status.state] || 'Chưa xác định'}${status.message ? ` — ${status.message}` : ''}`}
    />
  );
}

function ProxyField({ platform }: { platform: PlatformKey }) {
  const id = useId();
  const useProxy = useApp(state => state.scanSettings[platform].useProxy);
  const count = useApp(state => state.proxyCount);
  const busy = useApp(state => uiIsBusy(state));
  return (
    <ToggleField
      id={id}
      label="Proxy xoay"
      icon={<NetworkIcon />}
      checked={useProxy}
      onChange={value => void updateScanSettings(platform, { useProxy: value })}
      disabled={busy}
      switchDisabled={false}
      onConfigure={() => openModal('proxy')}
      dotClass={count > 0 ? (useProxy ? 'bg-success' : 'bg-primary') : ''}
      title={count > 0 ? `${count} proxy đã lưu` : 'Chưa có proxy — bấm Cấu hình để dán danh sách'}
    />
  );
}

function ScanFields() {
  const workersId = useId();
  const modeId = useId();
  const platform = useApp(state => state.activePlatform);
  const settings = useApp(state => state.scanSettings[state.activePlatform]);
  const busy = useApp(state => uiIsBusy(state));
  return (
    <div className="grid min-w-0 grid-cols-2 gap-3 sm:flex sm:flex-wrap sm:items-end">
      <div className="flex flex-col gap-1.5">
        <FieldLabel htmlFor={workersId} icon={<GaugeIcon />}>Luồng</FieldLabel>
        <NativeSelect id={workersId} className="sm:w-20" value={String(settings.workers)} disabled={busy} onChange={event => void updateScanSettings(platform, { workers: Number(event.target.value) })}>
          {WORKER_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1.5">
        <FieldLabel htmlFor={modeId} icon={<Settings2Icon />}>Chế độ</FieldLabel>
        <NativeSelect id={modeId} className="sm:w-44" value={settings.scrapeMode} disabled={busy} onChange={event => void updateScanSettings(platform, { scrapeMode: event.target.value as ScrapeMode })}>
          <option value="request">Request</option>
          <option value="hybrid">Hybrid</option>
          <option value="browser">Trình duyệt</option>
        </NativeSelect>
      </div>
      <ProxyField platform={platform} />
      <CookieField platform={platform} />
    </div>
  );
}

function ScanActions() {
  const phase = useApp(state => state.scan.phase);
  const busy = useApp(state => uiIsBusy(state));
  const blocked = useApp(state => (uiIsBusy(state) ? '' : startBlockedReason(state)));
  const reconnecting = useApp(state => state.connection !== '');
  const monthLabel = useApp(state => monthsOf(state).find(month => month.thang === state.rd.thang[state.activePlatform])?.label || '');
  const scanning = scanIsBusy(phase);
  const cancelDisabled = !scanning || phase === 'saving' || phase === 'cancelling' || reconnecting;
  return (
    <div className="flex items-end gap-2 max-sm:w-full">
      <Button variant="outline" onClick={cancelScraping} disabled={cancelDisabled} data-testid="cancel-scan" className="max-sm:flex-1">
        {phase === 'cancelling' ? <Loader2Icon className="animate-spin" /> : <CircleStopIcon />}
        {phase === 'cancelling' ? 'Đang hủy…' : 'Hủy'}
      </Button>
      <Tip content={blocked}>
        <span className="max-sm:flex-[2]">
          <Button onClick={() => void startRdScan()} disabled={busy || Boolean(blocked)} data-testid="start-scan" className="w-full sm:min-w-40">
            {busy ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
            {busy ? 'Đang quét…' : monthLabel ? `Quét ${monthLabel.replace(/^Tháng/, 'tháng')}` : 'Bắt đầu quét'}
          </Button>
        </span>
      </Tip>
    </div>
  );
}

/** Re-render every second while a retry cooldown counts down. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function ScanProgress() {
  const scan = useApp(state => state.scan);
  const active = useApp(state => state.activePlatform);
  const reconnecting = useApp(state => state.connection !== '');
  const now = useNow(Boolean(scan.retryWait));
  const progress = scan.progress;
  const platform = progress ? progressPlatform(progress, scan, active) : active;
  const counts = progress ? progressResultCounts(progress, scan.rows, platform) : { success: 0, hidden: 0, error: 0 };
  let [statusText, tone] = progressStatusView(scan.phase, counts, platform, progress?.stopReason);
  let line = progressLineText(progress, scan.phase);
  if (scan.retryWait) {
    line = retryWaitText(scan.retryWait, scan.retrySkipRequested, now);
    [statusText, tone] = ['Chờ quét lại', 'warn'];
  } else if (progress?.retryPass && scanIsBusy(scan.phase) && scan.phase !== 'saving') {
    [statusText, tone] = ['Đang quét lại link lỗi', ''];
  }
  const showSkip = Boolean(scan.retryWait) && scanIsBusy(scan.phase);
  return (
    <div className="flex flex-col gap-2" data-testid="scan-progress">
      <Progress value={scan.retryWait ? 100 : progressPercent(progress)} className={cn(scan.retryWait && '[&>div]:bg-warning')} />
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
        <span aria-live="polite" data-testid="progress-text" className="min-w-0 break-words" title={line}>{line}</span>
        <span className="flex items-center gap-2">
          {progress && (
            <span className="flex items-center gap-2 tabular-nums">
              <span className="text-success" title="Thành công">✓ {formatNumber(counts.success)}</span>
              <span className="text-warning" title="Không số liệu">◌ {formatNumber(counts.hidden)}</span>
              <span className="text-destructive" title="Lỗi">✕ {formatNumber(counts.error)}</span>
            </span>
          )}
          {statusText && <Badge variant={tone === 'success' ? 'success' : tone === 'warn' ? 'warning' : 'secondary'} data-testid="progress-status">{statusText}</Badge>}
          {showSkip && (
            <Button variant="outline" size="sm" className="h-7 px-2.5 text-xs" onClick={skipRetryWait} disabled={scan.retrySkipRequested || scan.phase === 'cancelling' || reconnecting} data-testid="skip-retry">
              <SkipForwardIcon /> Bỏ qua quét lại
            </Button>
          )}
        </span>
      </div>
    </div>
  );
}

export function ScanCard() {
  return (
    <section className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-xs" aria-label="Quét số liệu" data-testid="scan-toolbar">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <ScanFields />
        <ScanActions />
      </div>
      <ScanProgress />
    </section>
  );
}

// ---- Admin workspace: the link table plus the live scan views ------------------------

type AdminTab = Extract<WorkspaceTab, 'sheet' | 'live' | 'nostats' | 'errors' | 'duplicates' | 'logs'>;

const TABS: Array<{ key: AdminTab; label: string; icon: ReactNode; tone?: string }> = [
  { key: 'sheet', label: 'Danh sách link', icon: <Rows3Icon /> },
  { key: 'live', label: 'Kết quả quét', icon: <RadioIcon /> },
  { key: 'nostats', label: 'Không số liệu', icon: <EyeOffIcon />, tone: 'text-warning' },
  { key: 'errors', label: 'Lỗi quét', icon: <FileWarningIcon />, tone: 'text-destructive' },
  { key: 'duplicates', label: 'Link trùng', icon: <CopyPlusIcon /> },
  { key: 'logs', label: 'Nhật ký', icon: <TerminalIcon /> },
];

function useTabCounts(): Record<AdminTab, number | null> {
  const scan = useApp(state => state.scan);
  const logCount = useApp(state => state.logs.length);
  const rows = useApp(state => (state.rd.table.state === 'ready' ? state.rd.table.rows.length : null));
  return useMemo(() => ({
    sheet: rows,
    live: scan.rows.length ? new Set(scan.rows.map(row => row.key)).size : 0,
    nostats: failedLinksForKind(scan.failed, 'nostats').length,
    errors: failedLinksForKind(scan.failed, 'errors').length,
    duplicates: scan.duplicates.length,
    logs: logCount,
  }), [scan, logCount, rows]);
}

function TabActions({ tab }: { tab: AdminTab }) {
  if (tab === 'nostats' || tab === 'errors') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyFailedLinks(tab)}><CopyIcon />Copy link</Button>
        <Button variant="outline" size="sm" onClick={() => clearFailedLinks(tab)}><Trash2Icon />Xóa</Button>
      </>
    );
  }
  if (tab === 'duplicates') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyDuplicateLinks()}><CopyIcon />Copy link</Button>
        <Button variant="outline" size="sm" onClick={clearDuplicateLinks}><Trash2Icon />Xóa</Button>
      </>
    );
  }
  if (tab === 'logs') {
    return (
      <>
        <Button variant="outline" size="sm" onClick={() => void copyLogs()}><CopyIcon />Copy</Button>
        <Button variant="outline" size="sm" onClick={exportLogs}><DownloadIcon />Xuất</Button>
        <Button variant="outline" size="sm" onClick={clearLogs}><Trash2Icon />Xóa</Button>
      </>
    );
  }
  return null;
}

export function AdminWorkspace() {
  const stored = useApp(state => state.workspaceTab);
  const counts = useTabCounts();
  const tab: AdminTab = TABS.some(item => item.key === stored) ? (stored as AdminTab) : 'sheet';
  const tabs = TABS.filter(item => item.key !== 'duplicates' || (counts.duplicates ?? 0) > 0 || tab === 'duplicates');
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      <nav className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="tablist" aria-label="Các vùng dữ liệu">
        {tabs.map(item => {
          const selected = item.key === tab;
          const count = counts[item.key];
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
              <span className={item.tone}>{item.icon}</span>
              {item.label}
              {count !== null && (
                <span data-testid={`count-${item.key}`} className={cn('rounded-full px-1.5 py-px text-[11px] tabular-nums', selected ? 'bg-accent text-accent-foreground' : 'bg-muted text-muted-foreground')}>
                  {formatNumber(count)}
                </span>
              )}
            </button>
          );
        })}
      </nav>
      {tab === 'sheet' ? <LinkTablePanel /> : (
        <section className="flex min-h-[26rem] min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-xs md:max-h-[calc(100dvh-12rem)]">
          {tab !== 'live' && (
            <div className="flex flex-wrap items-center justify-end gap-1.5 border-b px-3 py-2"><TabActions tab={tab} /></div>
          )}
          {tab === 'live' && <LivePanel onlySuccess={false} hideSheet />}
          {(tab === 'nostats' || tab === 'errors') && <FailurePanel kind={tab} />}
          {tab === 'duplicates' && <DuplicatesPanel />}
          {tab === 'logs' && <LogsPanel />}
        </section>
      )}
    </div>
  );
}

