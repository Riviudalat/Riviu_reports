// Postgres storage (Riviu Data): the month-based view and scan actions of the V2 pages.
// The shared store, socket and cookie/proxy state live in ./app; this module adds the month
// list and table, the scan request for a month, and the socket reactions (storageHooks).
import { errorMessage } from '@/lib/api';
import type { Viewer } from '@/lib/auth';
import { fetchMonths, fetchRows, type MonthEntry } from '@/lib/riviuData';
import { PLATFORM_KEYS, SCRAPE_MODE_LABELS, isPlatform, platformConfig, type PlatformKey } from '@/lib/platforms';
import { clearLive, type RunContext } from '@/lib/scan';
import {
  addLog, appStore, canAdmin, connectWS, loadScanSettings, notify, openModal, refreshCookieStatus, refreshProxyStatus, sendScanCommand,
  storageHooks, swapShownScan, uiIsBusy, type AppState, type ModalName, type RdState,
} from './app';

const get = () => appStore.get();
const set = appStore.set;

const VIEW_KEY = 'riviuV2View';
const THANG_PATTERN = /^\d{4}-\d{2}$/;

function setRd(patch: Partial<RdState> | ((rd: RdState) => Partial<RdState>)) {
  set(state => ({ rd: { ...state.rd, ...(typeof patch === 'function' ? patch(state.rd) : patch) } }));
}

function savedPlatform(): PlatformKey | null {
  try {
    const value = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null')?.platform;
    return isPlatform(value) ? value : null;
  } catch {
    return null;
  }
}

function savePlatform(platform: PlatformKey) {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify({ platform })); } catch { /* storage unavailable */ }
}

// ---- Months and rows -------------------------------------------------------------------

const monthsSeq: Record<PlatformKey, number> = { tiktok: 0, threads: 0 };
let rowsSeq = 0;

/** Load a platform's month list; keep its chosen month when it still exists, else the default. */
export async function loadMonths(platform: PlatformKey): Promise<void> {
  const sequence = ++monthsSeq[platform];
  try {
    const { months, defaultThang } = await fetchMonths(platform);
    if (sequence !== monthsSeq[platform]) return;
    setRd(rd => {
      const chosen = rd.thang[platform];
      const keep = chosen && months.some(month => month.thang === chosen);
      return { months: { ...rd.months, [platform]: months }, thang: { ...rd.thang, [platform]: keep ? chosen : defaultThang } };
    });
  } catch (error) {
    if (sequence !== monthsSeq[platform]) return;
    setRd(rd => ({ months: { ...rd.months, [platform]: rd.months[platform] ?? [] } }));
    if (platform === get().activePlatform) setRd({ table: { state: 'error', text: errorMessage(error, 'Không tải được danh sách tháng.') } });
  }
}

/** Load the shown month's links. A refresh of the same month keeps the table until it answers. */
export async function loadRows(): Promise<void> {
  const sequence = ++rowsSeq;
  const state = get();
  const platform = state.activePlatform;
  const thang = state.rd.thang[platform];
  if (!thang) {
    // Months failed to load: keep that error on screen.
    if (state.rd.table.state !== 'error') setRd({ table: { state: 'empty' } });
    return;
  }
  const table = state.rd.table;
  const sameView = table.state === 'ready' && table.platform === platform && table.thang === thang;
  if (!sameView) setRd({ table: { state: 'loading' } });
  try {
    const { partners, rows } = await fetchRows(platform, thang);
    if (sequence !== rowsSeq) return;
    setRd({ table: { state: 'ready', platform, thang, partners, rows } });
  } catch (error) {
    if (sequence !== rowsSeq) return;
    setRd({ table: { state: 'error', text: errorMessage(error, 'Không tải được danh sách link.') } });
  }
}

export async function refreshView(): Promise<void> {
  await loadMonths(get().activePlatform);
  await loadRows();
}

export async function selectRdPlatform(platform: PlatformKey): Promise<void> {
  if (!isPlatform(platform) || platform === get().activePlatform) return;
  // Each platform keeps its own scan (they run side by side): park the shown one, show this one's.
  swapShownScan(platform);
  set({ workspaceTab: 'sheet' });
  savePlatform(platform);
  if (get().rd.months[platform] === null) await loadMonths(platform);
  await loadRows();
}

export async function selectThang(thang: string): Promise<void> {
  if (!THANG_PATTERN.test(thang)) return;
  const platform = get().activePlatform;
  setRd(rd => ({ thang: { ...rd.thang, [platform]: thang } }));
  await loadRows();
}

const NO_MONTHS: MonthEntry[] = [];

/** A stable value for store selectors (a new [] per call would loop useSyncExternalStore). */
export function monthsOf(state: AppState, platform: PlatformKey = state.activePlatform): MonthEntry[] {
  return state.rd.months[platform] ?? NO_MONTHS;
}

// ---- Scan ------------------------------------------------------------------------------

export interface RdScanRequest {
  logLine: string;
  payload: Record<string, unknown>;
}

/** The start payload for the shown platform and month, or why it cannot start. */
export function buildRdScanRequest(state: AppState): { request: RdScanRequest | null; problem?: { message: string; open?: ModalName } } {
  const platform = state.activePlatform;
  const config = platformConfig(platform);
  const thang = state.rd.thang[platform];
  const settings = state.scanSettings[platform];
  if (!thang) return { request: null, problem: { message: 'Chưa có tháng nào để quét.' } };
  const threads = state.cookies.threads;
  const useThreadsSession = config.cookieSession && settings.useSession;
  if (useThreadsSession && (!threads.configured || !threads.generation)) {
    return { request: null, problem: { message: 'Import và kiểm tra cookie Threads trước khi dùng phiên.', open: 'threadsCookie' } };
  }
  const useTikTokSession = config.accountCookie && settings.useSession;
  if (useTikTokSession && !state.cookies.tiktok.configured) {
    return { request: null, problem: { message: 'Import và kiểm tra cookie TikTok trước khi dùng phiên.', open: 'tiktokCookie' } };
  }
  if (settings.useProxy && state.proxyCount <= 0) {
    return { request: null, problem: { message: 'Bật proxy nhưng chưa có danh sách. Bấm Cấu hình để dán proxy.', open: 'proxy' } };
  }
  const month = monthsOf(state, platform).find(item => item.thang === thang);
  const modeLabel = SCRAPE_MODE_LABELS[settings.scrapeMode] || SCRAPE_MODE_LABELS.request;
  const extras = (settings.useProxy ? ' • proxy bật' : '') + (useThreadsSession ? ' • cookie Threads' : '') + (useTikTokSession ? ' • cookie TikTok' : '');
  return {
    request: {
      // Never include proxy text or cookies in the log line.
      logLine: `Bắt đầu quét ${config.label} • ${month?.label || thang} • ${modeLabel}${extras} • luồng: ${settings.workers}.`,
      payload: {
        action: 'start',
        platform,
        sheet_name: thang,
        workers: settings.workers,
        scrape_mode: settings.scrapeMode,
        use_proxy: settings.useProxy,
        proxy_text: '',
        partners: [],
        retry_failed: true,
        ...(useThreadsSession ? { use_threads_session: true, threads_session_generation: threads.generation } : {}),
        ...(useTikTokSession ? { use_tiktok_session: true } : {}),
      },
    },
  };
}

let starting = false;

export async function startRdScan(): Promise<void> {
  if (!canAdmin() || uiIsBusy() || starting) return;
  starting = true;
  try {
    const platform = get().activePlatform;
    // Check the cookie right before starting: it may have expired or been replaced elsewhere.
    if (get().scanSettings[platform].useSession) await refreshCookieStatus(platform === 'threads' ? 'threads' : 'tiktok');
    if (uiIsBusy()) return;
    const { request, problem } = buildRdScanRequest(get());
    if (!request) {
      if (problem) {
        notify(problem.message, 'warn');
        if (problem.open) openModal(problem.open);
      }
      return;
    }
    if (!sendScanCommand(request.payload)) return;
    addLog(request.logLine);
    set(s => ({ workspaceTab: 'live', scan: { ...clearLive(s.scan), phase: 'starting', terminalKey: '', retryWait: null, retrySkipRequested: false } }));
  } finally {
    starting = false;
  }
}

// ---- Socket reactions ------------------------------------------------------------------

let followedRunId = '';

/** An admin's page follows a new run to its platform and month once; viewers keep their view. */
export function followRun(context: RunContext) {
  if (!context.runId || context.runId === followedRunId) return;
  followedRunId = context.runId;
  if (!canAdmin()) return;
  const platform = isPlatform(context.platform) ? context.platform : get().activePlatform;
  const thang = context.sheetName && THANG_PATTERN.test(context.sheetName) ? context.sheetName : '';
  const state = get();
  if (platform === state.activePlatform && (!thang || thang === state.rd.thang[platform])) return;
  set({ activePlatform: platform });
  if (thang) setRd(rd => ({ thang: { ...rd.thang, [platform]: thang } }));
  void (async () => {
    if (get().rd.months[platform] === null) await loadMonths(platform);
    await loadRows();
  })();
}

/** A run saved numbers (finished, or the main pass before the retry cooldown): refresh what is shown. */
export function numbersSaved(context: RunContext | null, _finished: boolean) {
  const platform = isPlatform(context?.platform) ? context!.platform as PlatformKey : get().activePlatform;
  void (async () => {
    await loadMonths(platform);
    const state = get();
    const thang = context?.sheetName || '';
    if (platform === state.activePlatform && (!thang || thang === state.rd.thang[platform])) await loadRows();
  })();
}

export function installRiviuDataHooks() {
  storageHooks.followRun = followRun;
  storageHooks.numbersSaved = numbersSaved;
}

/** Test seam. */
export function resetRiviuDataModule() {
  followedRunId = '';
  rowsSeq = 0;
  starting = false;
  for (const key of PLATFORM_KEYS) monthsSeq[key] = 0;
}

// ---- Startup ---------------------------------------------------------------------------

export async function startRiviuDataApp(viewer: Viewer) {
  installRiviuDataHooks();
  const platform = savedPlatform() || get().activePlatform;
  set({ viewer, storage: 'postgres', activePlatform: platform, workspaceTab: 'sheet' });
  const admin = canAdmin();
  const adminLoads = admin
    ? Promise.all([loadScanSettings(), refreshProxyStatus(), refreshCookieStatus('threads'), refreshCookieStatus('tiktok')])
    : Promise.resolve();
  await loadMonths(platform);
  await loadRows();
  await adminLoads;
  connectWS();
}
