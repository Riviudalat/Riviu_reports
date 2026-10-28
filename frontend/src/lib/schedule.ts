// "Lịch tự động" (server mode, riviu/scheduler.py): daily HH:MM slots per platform and the
// scan options of those runs. GET/POST /schedule answer the overview below.
import { PLATFORM_KEYS, PLATFORMS, type PlatformKey, type ScrapeMode } from './platforms';

export interface ScheduleSlot {
  time: string;
  enabled: boolean;
}

export interface ScheduleOptions {
  workers: number;
  scrapeMode: ScrapeMode;
  useProxy: boolean;
  useSession: boolean;
  retryFailed: boolean;
}

export interface PlatformSchedule {
  slots: ScheduleSlot[];
  options: ScheduleOptions;
}

export interface Schedule {
  version: number;
  platforms: Record<PlatformKey, PlatformSchedule>;
}

export interface ScheduleRun {
  id?: string;
  platform?: string;
  slot?: string;
  scheduledFor?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  /** completed | skipped | stopped | cancelled | failed */
  status?: string;
  message?: string;
  sheet?: string;
  counts?: { total?: number; processed?: number; success?: number; error?: number; hidden?: number; loginRequired?: number };
}

export interface ScheduleOverview {
  schedule: Schedule;
  runs: ScheduleRun[];
  platforms: Partial<Record<PlatformKey, { nextRun?: string | null; lastRun?: ScheduleRun | null }>>;
  now?: string;
  timezone?: string;
  active?: boolean;
}

export const SLOT_PATTERN = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
export const MAX_SLOTS = 24;

export function defaultOptions(platform: PlatformKey): ScheduleOptions {
  return { workers: 10, scrapeMode: PLATFORMS[platform].defaultScrapeMode, useProxy: false, useSession: false, retryFailed: true };
}

export function emptySchedule(): Schedule {
  return { version: 1, platforms: Object.fromEntries(PLATFORM_KEYS.map((key): [PlatformKey, PlatformSchedule] => [key, { slots: [], options: defaultOptions(key) }])) as Record<PlatformKey, PlatformSchedule> };
}

/** Accept what the server sends, filling anything missing with the defaults. */
export function normalizeOverview(raw: unknown): ScheduleOverview {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const schedule = emptySchedule();
  const rawPlatforms = ((data.schedule as Record<string, unknown> | undefined)?.platforms || {}) as Record<string, unknown>;
  for (const key of PLATFORM_KEYS) {
    const item = (rawPlatforms[key] || {}) as Record<string, unknown>;
    const slots = Array.isArray(item.slots) ? item.slots : [];
    schedule.platforms[key] = {
      slots: slots
        .map(slot => slot as Record<string, unknown>)
        .filter(slot => typeof slot.time === 'string' && SLOT_PATTERN.test(slot.time))
        .map(slot => ({ time: String(slot.time), enabled: slot.enabled !== false })),
      options: { ...defaultOptions(key), ...((item.options || {}) as Partial<ScheduleOptions>) },
    };
  }
  return {
    schedule,
    runs: Array.isArray(data.runs) ? (data.runs as ScheduleRun[]) : [],
    platforms: (data.platforms || {}) as ScheduleOverview['platforms'],
    now: typeof data.now === 'string' ? data.now : undefined,
    timezone: typeof data.timezone === 'string' ? data.timezone : undefined,
    active: Boolean(data.active),
  };
}

/** Add a slot (sorted, no duplicates); returns an error message instead when it cannot. */
export function addSlot(slots: readonly ScheduleSlot[], time: string): ScheduleSlot[] | string {
  if (!SLOT_PATTERN.test(time)) return 'Giờ phải có dạng HH:MM (00:00–23:59).';
  if (slots.some(slot => slot.time === time)) return `Đã có khung giờ ${time}.`;
  if (slots.length >= MAX_SLOTS) return `Mỗi nền tảng có tối đa ${MAX_SLOTS} khung giờ.`;
  return [...slots, { time, enabled: true }].sort((a, b) => a.time.localeCompare(b.time));
}

/** "2026-10-28T21:00:00+07:00" -> "21:00 · 28/10" in the schedule's own clock (no browser TZ shift). */
export function formatScheduleTime(iso: string | null | undefined): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
  if (!match) return '';
  return `${match[4]}:${match[5]} · ${match[3]}/${match[2]}`;
}

export const RUN_STATUS: Record<string, { label: string; tone: 'success' | 'warning' | 'destructive' | 'secondary' }> = {
  completed: { label: 'Hoàn tất', tone: 'success' },
  skipped: { label: 'Bỏ qua', tone: 'secondary' },
  stopped: { label: 'Dừng sớm', tone: 'warning' },
  cancelled: { label: 'Đã hủy', tone: 'warning' },
  failed: { label: 'Lỗi', tone: 'destructive' },
};

export function runCountsText(run: ScheduleRun): string {
  const counts = run.counts;
  if (!counts || counts.total === undefined) return '';
  const parts = [`${counts.success ?? 0}/${counts.total} thành công`];
  if (counts.error) parts.push(`${counts.error} lỗi`);
  if (counts.hidden) parts.push(`${counts.hidden} không số liệu`);
  return parts.join(' · ');
}
