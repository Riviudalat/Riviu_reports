import { useId } from 'react';
import { CircleStopIcon, CookieIcon, GaugeIcon, Loader2Icon, NetworkIcon, PlayIcon, Settings2Icon, SlidersHorizontalIcon, Table2Icon } from 'lucide-react';
import { Dialog, SheetContent, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { NativeSelect, Switch, Tip, Separator } from '@/components/ui/misc';
import { PlatformIcon, useIsAdmin } from '@/components/common';
import { dataSheetsVisibleFirst, sheetOptionLabel, visibleSheetTabs } from '@/lib/sheets';
import { PLATFORMS, PLATFORM_KEYS, WORKER_OPTIONS, platformConfig, type ScrapeMode } from '@/lib/platforms';
import { scanIsBusy } from '@/lib/scan';
import { cn } from '@/lib/utils';
import {
  activeSource,
  appStore,
  cancelScraping,
  openModal,
  setPlatform,
  setScanSheet,
  setSettingsDrawer,
  setUseSession,
  startBlockedReason,
  startScraping,
  switchSheet,
  uiIsBusy,
  useApp,
  type CookieKind,
} from '@/state/app';

const COOKIE_STATE_TONE: Record<string, string> = {
  valid: 'bg-success', expired: 'bg-warning', invalid: 'bg-destructive', checkpoint: 'bg-warning', storage_unavailable: 'bg-destructive',
};

function FieldLabel({ htmlFor, icon, children }: { htmlFor?: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground [&_svg]:size-3.5">
      {icon}{children}
    </label>
  );
}

function ToggleCard({ id, label, icon, checked, onChange, disabled, switchDisabled = false, onConfigure, configureLabel, dotClass, title }: {
  id: string; label: string; icon: React.ReactNode; checked: boolean; onChange: (value: boolean) => void; disabled: boolean; switchDisabled?: boolean;
  onConfigure: () => void; configureLabel: string; dotClass: string; title: string;
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
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onConfigure} disabled={disabled} aria-label={configureLabel}>
          <SlidersHorizontalIcon /> Cấu hình
        </Button>
      </div>
    </div>
  );
}

function CookieToggle({ kind }: { kind: CookieKind }) {
  const id = useId();
  const status = useApp(state => state.cookies[kind]);
  const checked = useApp(state => (kind === 'threads' ? state.useThreadsSession : state.useTikTokSession));
  const busy = useApp(state => uiIsBusy(state));
  const label = kind === 'threads' ? 'Cookie Threads' : 'Cookie TikTok';
  const labels: Record<string, string> = { none: 'Chưa import', unchecked: 'Chưa kiểm tra', valid: 'Hợp lệ', expired: 'Hết hạn', invalid: 'Không hợp lệ', checkpoint: 'Cần xác minh', unknown: 'Chưa xác định', storage_unavailable: 'Kho bảo mật không khả dụng' };
  return (
    <ToggleCard
      id={id}
      label={label}
      icon={<CookieIcon />}
      checked={checked}
      onChange={value => setUseSession(kind, value)}
      disabled={busy}
      switchDisabled={!status.configured}
      onConfigure={() => openModal(kind === 'threads' ? 'threadsCookie' : 'tiktokCookie')}
      configureLabel={`Cấu hình ${label.toLowerCase()}`}
      dotClass={COOKIE_STATE_TONE[status.state] || ''}
      title={`${labels[status.state] || 'Chưa xác định'}${status.message ? ` — ${status.message}` : ''}`}
    />
  );
}

function ProxyToggle() {
  const id = useId();
  const useProxy = useApp(state => state.useProxy);
  const count = useApp(state => state.proxyCount);
  const busy = useApp(state => uiIsBusy(state));
  return (
    <ToggleCard
      id={id}
      label="Proxy xoay"
      icon={<NetworkIcon />}
      checked={useProxy}
      onChange={value => appStore.set({ useProxy: value })}
      disabled={busy}
      onConfigure={() => openModal('proxy')}
      configureLabel="Cấu hình proxy"
      dotClass={count > 0 ? (useProxy ? 'bg-success' : 'bg-primary') : ''}
      title={count > 0 ? `${count} proxy đã lưu — bấm Cấu hình để xem/sửa` : 'Bấm Cấu hình để dán proxy'}
    />
  );
}

/** Workers, mode, proxy and the platform's cookie session. */
function ScanSettingsFields({ stacked = false }: { stacked?: boolean }) {
  const workersId = useId();
  const modeId = useId();
  const platform = useApp(state => state.activePlatform);
  const workers = useApp(state => state.workers);
  const mode = useApp(state => state.scrapeModes[state.activePlatform]);
  const busy = useApp(state => uiIsBusy(state));
  const config = platformConfig(platform);
  return (
    <div className={cn('flex gap-3', stacked ? 'flex-col' : 'flex-wrap items-end')}>
      <div className="flex flex-col gap-1.5">
        <FieldLabel htmlFor={workersId} icon={<GaugeIcon />}>Luồng</FieldLabel>
        <NativeSelect id={workersId} className={stacked ? '' : 'w-20'} value={String(workers)} disabled={busy} onChange={event => appStore.set({ workers: Number(event.target.value) })}>
          {WORKER_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1.5">
        <FieldLabel htmlFor={modeId} icon={<Settings2Icon />}>Chế độ</FieldLabel>
        <NativeSelect
          id={modeId}
          className={stacked ? '' : 'w-56'}
          value={mode}
          disabled={busy}
          onChange={event => appStore.set(state => ({ scrapeModes: { ...state.scrapeModes, [state.activePlatform]: event.target.value as ScrapeMode } }))}
        >
          <option value="request">Request</option>
          <option value="hybrid">Hybrid (Request + trình duyệt)</option>
          <option value="browser">Trình duyệt</option>
        </NativeSelect>
      </div>
      <ProxyToggle />
      {config.accountCookie && <CookieToggle kind="tiktok" />}
      {config.cookieSession && <CookieToggle kind="threads" />}
    </div>
  );
}

function ScanSheetSelect() {
  const id = useId();
  const source = useApp(state => activeSource(state));
  const busy = useApp(state => uiIsBusy(state));
  const sheets = dataSheetsVisibleFirst(source.sheets, source.hiddenSheets);
  return (
    <div className="flex min-w-[11rem] flex-1 flex-col gap-1.5 sm:max-w-xs">
      <FieldLabel htmlFor={id} icon={<Table2Icon />}>Sheet quét</FieldLabel>
      <NativeSelect id={id} value={sheets.includes(source.scanSheet) ? source.scanSheet : ''} disabled={busy || !sheets.length} onChange={event => setScanSheet(event.target.value)}>
        {!sheets.length && <option value="">Không có sheet</option>}
        {sheets.map(sheet => <option key={sheet} value={sheet}>{sheetOptionLabel(sheet, source.hiddenSheets)}</option>)}
      </NativeSelect>
    </div>
  );
}

/** Viewers pick which sheet to look at (data sheets and this platform's summary tabs). */
function ViewSheetSelect() {
  const id = useId();
  const source = useApp(state => activeSource(state));
  const platform = useApp(state => state.activePlatform);
  const busy = useApp(state => state.sourceBusy || scanIsBusy(state.scan.phase));
  const visible = visibleSheetTabs(source.sheets, platform);
  const data = dataSheetsVisibleFirst(visible, source.hiddenSheets);
  const options = [...data, ...visible.filter(sheet => !data.includes(sheet))];
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5 sm:max-w-sm">
      <FieldLabel htmlFor={id} icon={<Table2Icon />}>Sheet đang xem</FieldLabel>
      <NativeSelect id={id} value={options.includes(source.displaySheet) ? source.displaySheet : ''} disabled={busy || !options.length} onChange={event => void switchSheet(event.target.value)}>
        {!options.length && <option value="">Không có sheet</option>}
        {options.map(sheet => <option key={sheet} value={sheet}>{sheetOptionLabel(sheet, source.hiddenSheets)}</option>)}
      </NativeSelect>
    </div>
  );
}

function ScanActions() {
  const phase = useApp(state => state.scan.phase);
  const busy = useApp(state => uiIsBusy(state));
  const blocked = useApp(state => (uiIsBusy(state) ? '' : startBlockedReason(state)));
  const reconnecting = useApp(state => state.connection !== '');
  const scanning = scanIsBusy(phase);
  const cancelDisabled = !scanning || phase === 'saving' || phase === 'cancelling' || reconnecting;
  return (
    <div className="flex items-end gap-2">
      <Button variant="outline" onClick={cancelScraping} disabled={cancelDisabled} data-testid="cancel-scan">
        {phase === 'cancelling' ? <Loader2Icon className="animate-spin" /> : <CircleStopIcon />}
        {phase === 'cancelling' ? 'Đang hủy...' : 'Hủy'}
      </Button>
      <Tip content={blocked}>
        <span>
          <Button onClick={() => startScraping()} disabled={busy || Boolean(blocked)} data-testid="start-scan" className="min-w-36">
            {busy ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
            {busy ? 'Đang quét...' : 'Bắt đầu quét'}
          </Button>
        </span>
      </Tip>
    </div>
  );
}

export function ScanToolbar() {
  const admin = useIsAdmin();
  const settingsOpen = useApp(state => state.settingsDrawerOpen);
  if (!admin) {
    return (
      <div className="flex flex-wrap items-end gap-3" data-testid="viewer-toolbar">
        <ViewSheetSelect />
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-end gap-3" role="toolbar" aria-label="Cấu hình quét" data-testid="scan-toolbar">
      <Button variant="outline" size="icon" className="lg:hidden" onClick={() => setSettingsDrawer(true)} aria-label="Cấu hình quét" title="Cấu hình quét">
        <Settings2Icon />
      </Button>
      <div className="hidden lg:block">
        <ScanSettingsFields />
      </div>
      <Separator orientation="vertical" className="hidden h-9 lg:block" />
      <ScanSheetSelect />
      <div className="ml-auto max-sm:w-full max-sm:[&>div]:justify-end">
        <ScanActions />
      </div>
      <Dialog open={settingsOpen} onOpenChange={open => setSettingsDrawer(open)}>
        <SheetContent className="max-w-sm">
          <div className="border-b px-5 py-4"><DialogTitle>Cấu hình quét</DialogTitle></div>
          <div className="overflow-y-auto p-5"><ScanSettingsFields stacked /></div>
        </SheetContent>
      </Dialog>
    </div>
  );
}

export function PlatformBar() {
  const active = useApp(state => state.activePlatform);
  const busy = useApp(state => uiIsBusy(state));
  return (
    <div className="inline-flex rounded-lg border bg-muted/60 p-1" role="group" aria-label="Nền tảng báo cáo">
      {PLATFORM_KEYS.map(key => {
        const selected = key === active;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={selected}
            disabled={busy}
            onClick={() => void setPlatform(key)}
            className={cn(
              'flex h-8 items-center gap-2 rounded-md px-3.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60',
              selected ? 'bg-card text-foreground shadow-sm ring-1 ring-border' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <PlatformIcon platform={key} />
            {PLATFORMS[key].label}
          </button>
        );
      })}
    </div>
  );
}
