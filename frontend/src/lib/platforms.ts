// Platform registry, ported from the PLATFORMS object in riviu/web/static/app.js.
// Everything that differs between platforms lives here.

export type PlatformKey = 'tiktok' | 'threads';
export type ScrapeMode = 'request' | 'hybrid' | 'browser';

export interface FailedItemLike {
  status?: string | null;
}

export interface PlatformConfig {
  key: PlatformKey;
  label: string;
  icon: string;
  defaultScrapeMode: ScrapeMode;
  linkHosts: readonly string[];
  linkPath: RegExp;
  liveLinkHeader: string;
  /** Fourth metric column: TikTok saves vs Threads reposts. */
  savedMetric: { column: string; label: string };
  previewKeepsTotalRow: boolean;
  /** Summary tabs are "Tổng kết <sheet>" (TikTok, legacy) or "Tổng kết <tag> <sheet>". */
  summaryTag: string;
  hidesResultSheetTabs: boolean;
  /** Threads cookie session ("Cookie Threads"). */
  cookieSession: boolean;
  /** Opt-in logged-in TikTok cookie ("Cookie TikTok"). */
  accountCookie: boolean;
  partialWithMetricsIsSuccess: boolean;
  reconcileCountsFromRows: boolean;
  noStatsTag: string;
  noStatsReason: (item: FailedItemLike) => string;
  completedWithIssues: (errorCount: number) => string;
}

const base = import.meta.env.BASE_URL;

export const PLATFORMS: Readonly<Record<PlatformKey, PlatformConfig>> = Object.freeze({
  tiktok: {
    key: 'tiktok',
    label: 'TikTok',
    icon: `${base}tiktok.svg`,
    defaultScrapeMode: 'request',
    linkHosts: ['tiktok.com', 'www.tiktok.com', 'm.tiktok.com', 'mobile.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'],
    linkPath: /^\/./,
    liveLinkHeader: 'LINK TIKTOK',
    savedMetric: { column: 'LƯỢT LƯU', label: 'Lượt lưu' },
    previewKeepsTotalRow: true,
    summaryTag: '',
    hidesResultSheetTabs: false,
    cookieSession: false,
    accountCookie: true,
    partialWithMetricsIsSuccess: false,
    reconcileCountsFromRows: false,
    noStatsTag: 'Ẩn số liệu',
    noStatsReason: () => 'Không đọc được số liệu (TikTok ẩn / không trả lượt xem)',
    completedWithIssues: () => 'Hoàn tất, có link thiếu số',
  },
  threads: {
    key: 'threads',
    label: 'Threads',
    icon: `${base}threads.svg`,
    defaultScrapeMode: 'hybrid',
    linkHosts: ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'],
    linkPath: /^(?:\/@[^/]+\/post\/|\/share\/)[A-Za-z0-9_-]+\/?$/,
    liveLinkHeader: 'LINK THREADS',
    savedMetric: { column: 'REPOST', label: 'Repost' },
    previewKeepsTotalRow: false,
    summaryTag: 'Threads',
    hidesResultSheetTabs: true,
    cookieSession: true,
    accountCookie: false,
    partialWithMetricsIsSuccess: true,
    reconcileCountsFromRows: true,
    noStatsTag: 'Thiếu số',
    noStatsReason: item => item.status || 'Không đọc được số liệu Threads',
    completedWithIssues: errorCount => (errorCount > 0 ? 'Hoàn tất, có link lỗi' : 'Hoàn tất, có link không trả số liệu'),
  },
});

export const PLATFORM_KEYS = Object.keys(PLATFORMS) as PlatformKey[];

export function isPlatform(value: unknown): value is PlatformKey {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(PLATFORMS, value);
}

export function platformConfig(platform: PlatformKey): PlatformConfig {
  return PLATFORMS[platform] ?? PLATFORMS[PLATFORM_KEYS[0]];
}

export const SCRAPE_MODE_LABELS: Readonly<Record<ScrapeMode, string>> = Object.freeze({
  browser: 'trình duyệt',
  hybrid: 'Hybrid (Request + trình duyệt)',
  request: 'Request (HTTP)',
});

export const WORKER_OPTIONS = [1, 3, 5, 8, 10, 20, 25, 30, 50] as const;
export const DEFAULT_WORKER_COUNT = 10;
