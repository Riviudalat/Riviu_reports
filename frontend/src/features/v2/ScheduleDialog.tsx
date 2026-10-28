import { useEffect, useId, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Popover as PopoverPrimitive } from 'radix-ui';
import { CalendarClockIcon, CalendarRangeIcon, Loader2Icon, PlusIcon, RefreshCwIcon, SaveIcon, Trash2Icon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge, Checkbox, Input, NativeSelect, Switch } from '@/components/ui/misc';
import { PlatformIcon } from '@/components/common';
import { errorMessage, getJson, postJson } from '@/lib/api';
import { PLATFORMS, PLATFORM_KEYS, WORKER_OPTIONS, isPlatform, type PlatformKey, type ScrapeMode } from '@/lib/platforms';
import { fetchMonths } from '@/lib/riviuData';
import {
  addSlot, formatScheduleTime, LATEST_MONTH, MONTH_PATTERN, normalizeMonths, normalizeOverview, RUN_STATUS, runCountsText, slotMonthLabel,
  type Schedule, type ScheduleOptions, type ScheduleOverview, type ScheduleSlot,
} from '@/lib/schedule';
import { cn } from '@/lib/utils';
import { closeModal, notify, useApp } from '@/state/app';

async function fetchSchedule(): Promise<ScheduleOverview> {
  const { response, data } = await getJson<Record<string, unknown>>('/schedule', { cache: 'no-store' });
  if (!response.ok) throw new Error(String(data.error || 'Không tải được hẹn giờ tự động.'));
  return normalizeOverview(data);
}

export function ScheduleDialog() {
  const open = useApp(state => state.modal === 'schedule');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-3xl">{open && <ScheduleBody />}</DialogContent>
    </Dialog>
  );
}

function ScheduleBody() {
  const active = useApp(state => state.activePlatform);
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['schedule'], queryFn: fetchSchedule, staleTime: 0 });
  const [draft, setDraft] = useState<Schedule | null>(null);
  const [platform, setPlatform] = useState<PlatformKey>(active);
  const [newTime, setNewTime] = useState('08:00');
  const [problem, setProblem] = useState('');
  const [saving, setSaving] = useState(false);
  const timeId = useId();
  useEffect(() => {
    if (query.data && !draft) setDraft(query.data.schedule);
  }, [query.data, draft]);
  const dirty = Boolean(draft && query.data && JSON.stringify(draft) !== JSON.stringify(query.data.schedule));
  const current = draft?.platforms[platform];

  const updatePlatform = (patch: Partial<Schedule['platforms'][PlatformKey]>) => {
    setDraft(previous => previous && { ...previous, platforms: { ...previous.platforms, [platform]: { ...previous.platforms[platform], ...patch } } });
  };
  const updateOptions = (patch: Partial<ScheduleOptions>) => {
    if (current) updatePlatform({ options: { ...current.options, ...patch } });
  };
  const add = () => {
    if (!current) return;
    const result = addSlot(current.slots, newTime);
    if (typeof result === 'string') { setProblem(result); return; }
    setProblem('');
    updatePlatform({ slots: result });
  };
  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const { response, data } = await postJson<Record<string, unknown>>('/schedule', draft);
      if (!response.ok) throw new Error(String(data.error || 'Không lưu được hẹn giờ tự động.'));
      const overview = normalizeOverview(data);
      client.setQueryData(['schedule'], overview);
      setDraft(overview.schedule);
      setProblem('');
      notify('Đã lưu hẹn giờ tự động.', 'success');
    } catch (error) {
      setProblem(errorMessage(error, 'Không lưu được hẹn giờ tự động.'));
    } finally {
      setSaving(false);
    }
  };

  const overview = query.data;
  const nextRun = overview?.platforms[platform]?.nextRun;
  return (
    <>
      <DialogHeader>
        <DialogTitle><CalendarClockIcon className="size-4 text-primary" /> Hẹn giờ tự động</DialogTitle>
        <DialogDescription>
          Mỗi ngày đến giờ đã đặt, máy chủ tự quét các tháng đã chọn của từng khung giờ. TikTok và Threads chạy cùng lúc. Giờ theo {overview?.timezone || 'Asia/Ho_Chi_Minh (UTC+7)'}.
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-5">
        {query.isPending && <p className="py-10 text-center text-sm text-muted-foreground">Đang tải hẹn giờ tự động…</p>}
        {query.isError && <p className="py-10 text-center text-sm text-destructive">{query.error.message}</p>}
        {draft && current && (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="inline-flex rounded-lg bg-muted p-1" role="tablist" aria-label="Nền tảng">
                {PLATFORM_KEYS.map(key => {
                  const count = draft.platforms[key].slots.filter(slot => slot.enabled).length;
                  return (
                    <button key={key} type="button" role="tab" aria-selected={key === platform} onClick={() => setPlatform(key)}
                      className={cn('flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium', key === platform ? 'bg-card shadow-sm ring-1 ring-border/70' : 'text-muted-foreground hover:text-foreground')}>
                      <PlatformIcon platform={key} /> {PLATFORMS[key].label}
                      <span className="rounded-full bg-background px-1.5 text-[11px] tabular-nums text-muted-foreground">{count}</span>
                    </button>
                  );
                })}
              </div>
              <p className="text-sm text-muted-foreground" data-testid="next-run">
                Lượt tới: <b className="font-semibold text-foreground">{formatScheduleTime(nextRun) || (dirty ? 'lưu để tính lại' : 'chưa có')}</b>
              </p>
            </div>

            <section className="flex flex-col gap-2.5">
              <h3 className="text-sm font-semibold">Khung giờ</h3>
              {current.slots.length === 0 && <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">Chưa có khung giờ nào cho {PLATFORMS[platform].label}.</p>}
              {current.slots.length > 0 && (
                <ul className="flex flex-col gap-2" data-testid="slot-list">
                  {current.slots.map(slot => (
                    <li key={slot.time} className={cn('flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border px-3 py-2', !slot.enabled && 'bg-muted/40')}>
                      <span className={cn('w-14 text-base font-semibold tabular-nums', !slot.enabled && 'text-muted-foreground line-through')}>{slot.time}</span>
                      <SlotMonths platform={platform} slot={slot}
                        onChange={months => updatePlatform({ slots: current.slots.map(item => (item.time === slot.time ? { ...item, months } : item)) })} />
                      <span className="ml-auto flex items-center gap-1.5">
                        <Switch checked={slot.enabled} aria-label={`Bật khung giờ ${slot.time}`}
                          onCheckedChange={value => updatePlatform({ slots: current.slots.map(item => (item.time === slot.time ? { ...item, enabled: value } : item)) })} />
                        <Button variant="ghost" size="icon-sm" aria-label={`Xóa khung giờ ${slot.time}`} onClick={() => updatePlatform({ slots: current.slots.filter(item => item.time !== slot.time) })}>
                          <Trash2Icon className="text-muted-foreground" />
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex items-center gap-2">
                <label htmlFor={timeId} className="sr-only">Giờ mới</label>
                <Input id={timeId} type="time" className="w-32" value={newTime} onChange={event => setNewTime(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') add(); }} />
                <Button variant="soft" onClick={add}><PlusIcon /> Thêm giờ</Button>
              </div>
            </section>

            <section className="flex flex-col gap-2.5">
              <h3 className="text-sm font-semibold">Tùy chọn quét {PLATFORMS[platform].label}</h3>
              <div className="grid gap-3 rounded-lg border p-3 sm:grid-cols-2">
                <label className="flex items-center justify-between gap-3 text-sm">
                  Luồng
                  <NativeSelect className="w-24" value={String(current.options.workers)} onChange={event => updateOptions({ workers: Number(event.target.value) })} aria-label="Số luồng">
                    {WORKER_OPTIONS.map(value => <option key={value} value={value}>{value}</option>)}
                  </NativeSelect>
                </label>
                <label className="flex items-center justify-between gap-3 text-sm">
                  Chế độ
                  <NativeSelect className="w-36" value={current.options.scrapeMode} onChange={event => updateOptions({ scrapeMode: event.target.value as ScrapeMode })} aria-label="Chế độ quét">
                    <option value="request">Request</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="browser">Trình duyệt</option>
                  </NativeSelect>
                </label>
                <OptionSwitch label="Proxy xoay" checked={current.options.useProxy} onChange={value => updateOptions({ useProxy: value })} />
                <OptionSwitch label={`Cookie ${PLATFORMS[platform].label}`} checked={current.options.useSession} onChange={value => updateOptions({ useSession: value })} />
                <OptionSwitch label="Tự quét lại link lỗi" hint="Sau lượt chính, chờ rồi quét lại các link lỗi tạm thời." checked={current.options.retryFailed} onChange={value => updateOptions({ retryFailed: value })} className="sm:col-span-2" />
              </div>
            </section>

            {problem && <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{problem}</p>}

            <section className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold">Lượt chạy gần đây</h3>
                {overview?.active && <Badge variant="brand"><Loader2Icon className="animate-spin" /> Đang chạy</Badge>}
              </div>
              <RunList runs={overview?.runs ?? []} />
            </section>
          </>
        )}
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" className="mr-auto" onClick={() => { setDraft(null); void query.refetch(); }} disabled={query.isFetching}>
          <RefreshCwIcon className={query.isFetching ? 'animate-spin' : ''} /> Tải lại
        </Button>
        <Button variant="outline" onClick={closeModal}>Đóng</Button>
        <Button onClick={() => void save()} disabled={!dirty || saving} data-testid="save-schedule">
          {saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />} Lưu lịch
        </Button>
      </DialogFooter>
    </>
  );
}

/** The months a slot scans: chips, and a picker with "Tháng mới nhất" plus the platform's months. */
function SlotMonths({ platform, slot, onChange }: { platform: PlatformKey; slot: ScheduleSlot; onChange: (months: string[]) => void }) {
  const months = useQuery({ queryKey: ['rd-months', platform], queryFn: () => fetchMonths(platform), staleTime: 60_000 });
  const choices = [LATEST_MONTH, ...(months.data?.months ?? []).map(month => month.thang).filter(thang => MONTH_PATTERN.test(thang))];
  const toggle = (value: string, checked: boolean) => {
    const next = checked ? [...slot.months, value] : slot.months.filter(item => item !== value);
    if (next.length) onChange(normalizeMonths(next));
  };
  return (
    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5" data-testid={`slot-months-${slot.time}`}>
      {slot.months.map(value => (
        <span key={value} className={cn('rounded-md px-1.5 py-0.5 text-xs font-medium', value === LATEST_MONTH ? 'bg-primary/12 text-primary' : 'bg-muted text-foreground/80')}>
          {slotMonthLabel(value)}
        </span>
      ))}
      <PopoverPrimitive.Root>
        <PopoverPrimitive.Trigger asChild>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" aria-label={`Chọn tháng cho ${slot.time}`}>
            <CalendarRangeIcon /> Chọn tháng
          </Button>
        </PopoverPrimitive.Trigger>
        <PopoverPrimitive.Portal>
          <PopoverPrimitive.Content align="start" sideOffset={6} className="z-50 w-64 rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg">
            <p className="px-2 pb-1 pt-1.5 text-xs text-muted-foreground">Quét lần lượt các tháng đã chọn lúc {slot.time}.</p>
            <ul className="max-h-72 overflow-y-auto" aria-label={`Tháng quét lúc ${slot.time}`}>
              {months.isPending && <li className="px-2 py-2 text-xs text-muted-foreground">Đang tải danh sách tháng…</li>}
              {choices.map(value => {
                const checked = slot.months.includes(value);
                const id = `month-${slot.time}-${value}`;
                const count = months.data?.months.find(month => month.thang === value)?.rows;
                return (
                  <li key={value}>
                    <label htmlFor={id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent">
                      <Checkbox id={id} checked={checked} disabled={checked && slot.months.length === 1} onCheckedChange={value2 => toggle(value, value2 === true)} />
                      <span className="flex-1">{slotMonthLabel(value)}{value === LATEST_MONTH && <span className="block text-[11px] text-muted-foreground">Tự chuyển khi có tháng mới</span>}</span>
                      {count !== undefined && <span className="text-xs tabular-nums text-muted-foreground">{count} link</span>}
                    </label>
                  </li>
                );
              })}
            </ul>
          </PopoverPrimitive.Content>
        </PopoverPrimitive.Portal>
      </PopoverPrimitive.Root>
    </span>
  );
}

function OptionSwitch({ label, hint, checked, onChange, className }: { label: string; hint?: string; checked: boolean; onChange: (value: boolean) => void; className?: string }) {
  const id = useId();
  return (
    <div className={cn('flex items-center justify-between gap-3 text-sm', className)}>
      <label htmlFor={id} className="min-w-0">
        {label}
        {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
      </label>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function RunList({ runs }: { runs: ScheduleOverview['runs'] }) {
  if (!runs.length) return <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">Chưa có lượt chạy nào.</p>;
  return (
    <ul className="divide-y rounded-lg border" data-testid="run-list">
      {runs.slice(0, 15).map((run, index) => {
        const status = RUN_STATUS[run.status || ''] || { label: run.status || '—', tone: 'secondary' as const };
        const platform = isPlatform(run.platform) ? run.platform : null;
        const counts = runCountsText(run);
        return (
          <li key={run.id || index} className="flex flex-col gap-1 px-3 py-2.5 text-sm sm:flex-row sm:items-start sm:gap-3">
            <span className="flex shrink-0 items-center gap-2 sm:w-44">
              {platform && <PlatformIcon platform={platform} />}
              <span className="font-medium tabular-nums">{formatScheduleTime(run.startedAt || run.scheduledFor)}</span>
              <Badge variant={status.tone}>{status.label}</Badge>
            </span>
            <span className="min-w-0 flex-1 text-muted-foreground">
              {run.sheet && MONTH_PATTERN.test(run.sheet) && <b className="mr-1 font-medium text-foreground">{slotMonthLabel(run.sheet)}:</b>}
              {run.message}
              {counts && <span className="mt-0.5 block text-xs text-foreground/80 tabular-nums">{counts}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
