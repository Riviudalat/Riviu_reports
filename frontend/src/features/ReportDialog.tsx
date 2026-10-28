import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DownloadIcon, Loader2Icon, RefreshCwIcon, SearchIcon, XIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Checkbox, Input, NativeSelect } from '@/components/ui/misc';
import { AdminOnly, PlatformIcon } from '@/components/common';
import { apiFetch, errorMessage, getJson } from '@/lib/api';
import { downloadBlob, filenameFromDisposition } from '@/lib/format';
import { buildExportFallbackFilename, DEFAULT_MIN_VIEWS, normalizeReportPartners, partnerLinkStatusText, reportFilterParams, type ReportPartner } from '@/lib/partners';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import { scanIsBusy } from '@/lib/scan';
import { dataSheetsVisibleFirst, filterDataSheets, sheetOptionLabel } from '@/lib/sheets';
import { activeSource, closeModal, notify, startScraping, useApp, type AppState } from '@/state/app';

interface PartnersResponse {
  partners: ReportPartner[];
  sheets: string[];
  hiddenSheets: string[];
  currentSheet: string;
  fileLabel: string;
}

/** Query params of /report-partners: always the explicit file_id + platform of this source. */
export function reportPartnersParams(platform: PlatformKey, fileId: string, sheetName: string, applyMinViews: boolean, minViews: number): URLSearchParams {
  const params = new URLSearchParams();
  if (sheetName) params.set('sheet_name', sheetName);
  params.set('apply_min_views', applyMinViews ? 'true' : 'false');
  params.set('min_views', String(minViews));
  params.set('platform', platform);
  params.set('file_id', fileId);
  return params;
}

/** The export request body: explicit file_id + platform, the chosen partners and filter. */
export function exportReportBody(platform: PlatformKey, fileId: string, partners: string[], sheetName: string, applyMinViews: boolean, minViews: number) {
  return { partners, applyMinViews, minViews, sheetName, platform, file_id: fileId };
}

async function fetchPartners(platform: PlatformKey, fileId: string, sheetName: string, applyMinViews: boolean, minViews: number): Promise<PartnersResponse> {
  const { response, data } = await getJson<Record<string, unknown>>(`/report-partners?${reportPartnersParams(platform, fileId, sheetName, applyMinViews, minViews)}`);
  if (!response.ok) throw new Error(String(data.error || 'Không tải được danh sách đối tác'));
  const sheetList = Array.isArray(data.sheets) && data.sheets.length ? filterDataSheets(data.sheets) : filterDataSheets(data.allSheets as string[]);
  return {
    partners: normalizeReportPartners(data.partners),
    sheets: sheetList,
    hiddenSheets: Array.isArray(data.hiddenSheets) ? (data.hiddenSheets as string[]) : [],
    currentSheet: String(data.currentSheet || data.dataSheet || sheetName || sheetList[0] || ''),
    fileLabel: String(data.fileLabel || data.file || ''),
  };
}

/** Open on the sheet being scanned/viewed in this source; '' lets the server pick. */
function defaultReportSheet(state: AppState): string {
  const source = activeSource(state);
  const known = source.sheets || [];
  return filterDataSheets([source.scanSheet, source.displaySheet]).find(sheet => !known.length || known.includes(sheet)) || '';
}

export function ReportDialog() {
  const open = useApp(state => state.modal === 'report');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-4xl">{open && <ReportBody />}</DialogContent>
    </Dialog>
  );
}

function ReportBody() {
  const platform = useApp(state => state.activePlatform);
  const fileId = useApp(state => activeSource(state).fileId);
  const sourceHidden = useApp(state => activeSource(state).hiddenSheets);
  const initialSheet = useApp(defaultReportSheet);
  const scanning = useApp(state => scanIsBusy(state.scan.phase));
  const sourceBusy = useApp(state => state.sourceBusy);
  const [sheet, setSheet] = useState(initialSheet);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [applyMinViews, setApplyMinViews] = useState(true);
  const [minViewsInput, setMinViewsInput] = useState(String(DEFAULT_MIN_VIEWS));
  // Like the old input's change event: the list reloads when the value is committed, not per keystroke.
  const [minViewsCommitted, setMinViewsCommitted] = useState(String(DEFAULT_MIN_VIEWS));
  const [exporting, setExporting] = useState(false);
  const { minViews } = reportFilterParams(applyMinViews, minViewsCommitted);
  const config = platformConfig(platform);

  const query = useQuery({
    queryKey: ['report-partners', platform, fileId, sheet, applyMinViews, minViews],
    queryFn: () => fetchPartners(platform, fileId, sheet, applyMinViews, minViews),
    enabled: Boolean(fileId),
    staleTime: 0,
  });
  const data = query.data;
  const resolvedSheet = data?.currentSheet || sheet;
  // A new sheet or filter reloads the list; the old selection belongs to the old list.
  useEffect(() => { setSelected(new Set()); }, [platform, fileId, sheet, applyMinViews, minViews]);

  const partners = useMemo(() => data?.partners ?? [], [data]);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return partners.filter(item => item.name.toLowerCase().includes(needle));
  }, [partners, search]);
  const chosen = partners.filter(item => selected.has(item.name));
  const sheetOptions = dataSheetsVisibleFirst(data?.sheets ?? [], data?.hiddenSheets ?? sourceHidden);

  const toggle = (name: string, value: boolean) => setSelected(previous => {
    const next = new Set(previous);
    if (value) next.add(name); else next.delete(name);
    return next;
  });

  const exportReport = async () => {
    const names = chosen.map(item => item.name);
    if (!fileId || sourceBusy || !names.length) return;
    setExporting(true);
    try {
      const response = await apiFetch('/export-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exportReportBody(platform, fileId, names, resolvedSheet, applyMinViews, minViews)),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || 'Không xuất được báo cáo');
      }
      const blob = await response.blob();
      const filename = filenameFromDisposition(response.headers.get('Content-Disposition') || '', buildExportFallbackFilename(names, resolvedSheet));
      downloadBlob(blob, filename);
      notify(`Đã xuất báo cáo: ${filename}`, 'success');
      closeModal();
    } catch (error) {
      notify(`Lỗi xuất báo cáo: ${errorMessage(error, '')}`, 'error');
    } finally {
      setExporting(false);
    }
  };

  const refreshPartners = () => {
    const names = chosen.map(item => item.name);
    if (!names.length) return;
    closeModal();
    startScraping(names, resolvedSheet);
  };

  const subtitle = data ? `${data.fileLabel} • ${resolvedSheet || '—'} • ${partners.length} đối tác` : 'Chọn đối tác để cập nhật lại link hoặc xuất báo cáo.';

  return (
    <>
      <DialogHeader>
        <DialogTitle><PlatformIcon platform={platform} /> Đối tác & báo cáo {config.label}</DialogTitle>
        <DialogDescription>{fileId ? subtitle : 'Chưa chọn nguồn cho nền tảng này.'}</DialogDescription>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-0 flex-1 items-center gap-2 text-sm sm:max-w-xs">
            <span className="shrink-0 text-muted-foreground">Chọn sheet</span>
            <NativeSelect className="flex-1" value={sheetOptions.includes(resolvedSheet) ? resolvedSheet : ''} disabled={!sheetOptions.length} onChange={event => event.target.value && setSheet(event.target.value)} aria-label="Chọn sheet báo cáo">
              {!sheetOptions.length && <option value="">Không có sheet</option>}
              {sheetOptions.map(name => <option key={name} value={name}>{sheetOptionLabel(name, data?.hiddenSheets ?? sourceHidden)}</option>)}
            </NativeSelect>
          </label>
          <div className="relative min-w-40 flex-1">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input type="search" placeholder="Tìm đối tác..." className="pl-8" value={search} onChange={event => setSearch(event.target.value)} aria-label="Tìm đối tác" />
          </div>
          <Button variant="outline" size="sm" onClick={() => setSelected(new Set(partners.map(item => item.name)))}>Chọn tất cả</Button>
          <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>Bỏ chọn</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/30 px-3 py-2 text-sm">
          <label className="flex cursor-pointer select-none items-center gap-2">
            <Checkbox checked={applyMinViews} onCheckedChange={value => setApplyMinViews(value === true)} aria-label="Lọc link dưới ngưỡng view" />
            Lọc link dưới
          </label>
          <Input type="number" min={0} step={10} className="h-8 w-24" value={minViewsInput} disabled={!applyMinViews} onChange={event => setMinViewsInput(event.target.value)}
            onBlur={() => setMinViewsCommitted(minViewsInput)}
            onKeyDown={event => { if (event.key === 'Enter') setMinViewsCommitted(minViewsInput); }}
            aria-label="Ngưỡng view"
          />
          <span className="text-muted-foreground">view</span>
        </div>
        <div className="grid min-h-0 gap-3 md:grid-cols-[1fr_16rem]">
          <div className="flex min-h-0 flex-col rounded-lg border">
            <div className="border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Danh sách đối tác</div>
            <div className="max-h-[42vh] min-h-40 overflow-y-auto p-1" data-testid="partner-list">
              {(query.isPending && fileId) && <p className="p-6 text-center text-sm text-muted-foreground">Đang tải danh sách đối tác...</p>}
              {query.isError && <p className="p-6 text-center text-sm text-destructive">{query.error.message}</p>}
              {query.isSuccess && visible.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">Không tìm thấy đối tác phù hợp.</p>}
              {visible.map(item => {
                const status = partnerLinkStatusText(item);
                return (
                  <label key={item.name} className="flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-sm hover:bg-muted/60">
                    <Checkbox checked={selected.has(item.name)} onCheckedChange={value => toggle(item.name, value === true)} aria-label={item.name} />
                    <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                    {status && <span className="shrink-0 text-xs text-muted-foreground">{status}</span>}
                  </label>
                );
              })}
            </div>
          </div>
          <div className="flex min-h-0 flex-col rounded-lg border">
            <div className="flex items-center justify-between border-b px-3 py-1.5">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Đã chọn {chosen.length}</span>
              <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={!chosen.length} onClick={() => setSelected(new Set())}>Xóa hết</Button>
            </div>
            <div className="max-h-[42vh] min-h-24 overflow-y-auto p-1">
              {chosen.length === 0 && <p className="p-4 text-center text-sm text-muted-foreground">Chưa chọn đối tác nào.</p>}
              {chosen.map(item => (
                <div key={item.name} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted/60">
                  <span className="min-w-0 flex-1 truncate" title={item.name}>{item.name}</span>
                  <button type="button" className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={`Bỏ chọn ${item.name}`} onClick={() => toggle(item.name, false)}>
                    <XIcon className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
        <p className="text-sm text-muted-foreground" data-testid="report-summary">
          {chosen.length === 0 ? 'Chưa chọn đối tác nào.' : `Đã chọn ${chosen.length}/${partners.length} đối tác.`}
        </p>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={closeModal}>Hủy</Button>
        <AdminOnly>
          <Button variant="soft" disabled={!chosen.length || scanning} onClick={refreshPartners} data-testid="refresh-partners">
            <RefreshCwIcon /> Cập nhật đối tác
          </Button>
        </AdminOnly>
        <Button disabled={!chosen.length || exporting || !fileId} onClick={() => void exportReport()} data-testid="export-report">
          {exporting ? <Loader2Icon className="animate-spin" /> : <DownloadIcon />}
          {exporting ? 'Đang xuất...' : 'Xuất báo cáo'}
        </Button>
      </DialogFooter>
    </>
  );
}
