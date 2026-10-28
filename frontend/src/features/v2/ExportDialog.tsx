import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DownloadIcon, Loader2Icon, SearchIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Checkbox, Input } from '@/components/ui/misc';
import { PlatformIcon } from '@/components/common';
import { apiFetch, errorMessage, getJson } from '@/lib/api';
import { downloadBlob, filenameFromDisposition, formatNumber, normalizeVietnameseKey } from '@/lib/format';
import { buildExportFallbackFilename, DEFAULT_MIN_VIEWS, normalizeReportPartners, partnerLinkStatusText, reportFilterParams, type ReportPartner } from '@/lib/partners';
import { platformConfig, type PlatformKey } from '@/lib/platforms';
import { exportBody, monthLabel, reportPartnersQuery } from '@/lib/riviuData';
import { cn } from '@/lib/utils';
import { closeModal, notify, useApp } from '@/state/app';
import { monthsOf } from '@/state/riviuData';

async function fetchReportPartners(platform: PlatformKey, thang: string, applyMinViews: boolean, minViews: number): Promise<ReportPartner[]> {
  const { response, data } = await getJson<{ partners?: unknown; error?: string }>(`/riviu-data/report-partners?${reportPartnersQuery(platform, thang, applyMinViews, minViews)}`);
  if (!response.ok) throw new Error(data.error || 'Không tải được danh sách đối tác');
  return normalizeReportPartners(data.partners).sort((a, b) => a.name.localeCompare(b.name, 'vi'));
}

export function ExportDialog() {
  const open = useApp(state => state.modal === 'export');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-2xl">{open && <ExportBody />}</DialogContent>
    </Dialog>
  );
}

function ExportBody() {
  const platform = useApp(state => state.activePlatform);
  const thang = useApp(state => state.rd.thang[state.activePlatform]);
  const label = useApp(state => monthsOf(state).find(month => month.thang === thang)?.label) || monthLabel(thang);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [applyMinViews, setApplyMinViews] = useState(true);
  const [minViewsInput, setMinViewsInput] = useState(String(DEFAULT_MIN_VIEWS));
  // The list reloads when the value is committed (blur / Enter), not per keystroke.
  const [minViewsCommitted, setMinViewsCommitted] = useState(String(DEFAULT_MIN_VIEWS));
  const [exporting, setExporting] = useState(false);
  const { minViews } = reportFilterParams(applyMinViews, minViewsCommitted);
  const config = platformConfig(platform);

  const query = useQuery({
    queryKey: ['rd-report-partners', platform, thang, applyMinViews, minViews],
    queryFn: () => fetchReportPartners(platform, thang, applyMinViews, minViews),
    enabled: Boolean(thang),
    staleTime: 0,
  });
  const partners = useMemo(() => query.data ?? [], [query.data]);
  // A new filter gives a new list; keep only the names it still has.
  useEffect(() => {
    setSelected(previous => new Set([...previous].filter(name => partners.some(item => item.name === name))));
  }, [partners]);
  const visible = useMemo(() => {
    const needle = normalizeVietnameseKey(search);
    return needle ? partners.filter(item => normalizeVietnameseKey(item.name).includes(needle)) : partners;
  }, [partners, search]);
  const chosen = partners.filter(item => selected.has(item.name));
  const allVisibleSelected = visible.length > 0 && visible.every(item => selected.has(item.name));

  const toggle = (name: string, value: boolean) => setSelected(previous => {
    const next = new Set(previous);
    if (value) next.add(name); else next.delete(name);
    return next;
  });
  const toggleVisible = () => setSelected(previous => {
    const next = new Set(previous);
    for (const item of visible) {
      if (allVisibleSelected) next.delete(item.name); else next.add(item.name);
    }
    return next;
  });

  const exportReport = async () => {
    const names = chosen.map(item => item.name);
    if (!thang || !names.length) return;
    setExporting(true);
    try {
      const response = await apiFetch('/riviu-data/export-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(exportBody(platform, thang, names, applyMinViews, minViews)),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || 'Không xuất được báo cáo');
      }
      const blob = await response.blob();
      const filename = filenameFromDisposition(response.headers.get('Content-Disposition') || '', buildExportFallbackFilename(names, thang));
      downloadBlob(blob, filename);
      notify(`Đã xuất báo cáo: ${filename}`, 'success');
      closeModal();
    } catch (error) {
      notify(`Lỗi xuất báo cáo: ${errorMessage(error, '')}`, 'error');
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle><PlatformIcon platform={platform} /> Xuất báo cáo {config.label}</DialogTitle>
        <DialogDescription>{label} • {query.isSuccess ? `${partners.length} đối tác` : 'Chọn đối tác để tải báo cáo Excel.'}</DialogDescription>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm">
          <label className="flex cursor-pointer select-none items-center gap-2">
            <Checkbox checked={applyMinViews} onCheckedChange={value => setApplyMinViews(value === true)} aria-label="Bỏ link dưới ngưỡng lượt xem" />
            Bỏ link dưới
          </label>
          <Input type="number" min={0} step={10} inputMode="numeric" className="h-8 w-24" value={minViewsInput} disabled={!applyMinViews}
            onChange={event => setMinViewsInput(event.target.value)}
            onBlur={() => setMinViewsCommitted(minViewsInput)}
            onKeyDown={event => { if (event.key === 'Enter') setMinViewsCommitted(minViewsInput); }}
            aria-label="Ngưỡng lượt xem" />
          <span className="text-muted-foreground">lượt xem</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative min-w-0 flex-1">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input type="search" placeholder="Tìm đối tác…" className="pl-8" value={search} onChange={event => setSearch(event.target.value)} aria-label="Tìm đối tác" />
          </div>
          <Button variant="outline" size="sm" className="h-9" onClick={toggleVisible} disabled={!visible.length} data-testid="toggle-all">
            {allVisibleSelected ? 'Bỏ chọn' : 'Chọn tất cả'}
          </Button>
        </div>
        <div className="max-h-[min(46vh,26rem)] min-h-40 overflow-y-auto rounded-lg border p-1" data-testid="partner-list">
          {query.isPending && <p className="p-6 text-center text-sm text-muted-foreground">Đang tải danh sách đối tác…</p>}
          {query.isError && <p className="p-6 text-center text-sm text-destructive">{query.error.message}</p>}
          {query.isSuccess && visible.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">{partners.length ? 'Không tìm thấy đối tác phù hợp.' : 'Tháng này chưa có đối tác nào.'}</p>}
          {visible.map(item => {
            const note = partnerLinkStatusText(item);
            const exportable = Number(item.linkCount) > 0;
            return (
              <label key={item.name} className="flex cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-sm hover:bg-muted/60">
                <Checkbox checked={selected.has(item.name)} onCheckedChange={value => toggle(item.name, value === true)} aria-label={item.name} />
                <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                {note
                  ? <span className="shrink-0 text-right text-xs text-muted-foreground">{note}</span>
                  : <span className={cn('shrink-0 rounded-full px-2 py-0.5 text-xs tabular-nums', exportable ? 'bg-accent text-accent-foreground' : 'bg-muted text-muted-foreground')}>{formatNumber(item.linkCount)} link</span>}
              </label>
            );
          })}
        </div>
        <p className="text-sm text-muted-foreground" data-testid="export-summary" aria-live="polite">
          {chosen.length === 0 ? 'Chưa chọn đối tác nào.' : chosen.length === 1 ? 'Sẽ tải 1 file Excel.' : `Đã chọn ${chosen.length}/${partners.length} đối tác — tải về 1 file .zip.`}
        </p>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={closeModal}>Đóng</Button>
        <Button disabled={!chosen.length || exporting || !thang} onClick={() => void exportReport()} data-testid="export-report">
          {exporting ? <Loader2Icon className="animate-spin" /> : <DownloadIcon />}
          {exporting ? 'Đang xuất…' : 'Tải báo cáo'}
        </Button>
      </DialogFooter>
    </>
  );
}
