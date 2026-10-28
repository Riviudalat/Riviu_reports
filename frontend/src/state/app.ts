// Application state and actions: a port of the orchestration in riviu/web/static/app.js.
// React components read slices with useApp(selector) and call the exported actions.
// Invariants kept from app.js:
// - every workbook request names its file_id + platform explicitly (sourceQuery), and a
//   platform never falls back to the other platform's workbook;
// - read responses are dropped when the source, platform, sheet or request sequence moved on;
// - socket events of another run are dropped or reset the live view (lib/scan.ts).
import { toast } from 'sonner';
import { apiFetch, bootstrapLocalSession, deleteJson, errorMessage, getJson, postForm, postJson } from '@/lib/api';
import { isAdmin, type Viewer } from '@/lib/auth';
import { copyText, downloadBlob, fileTimestamp } from '@/lib/format';
import { platformPreview, type PreviewRow } from '@/lib/links';
import { PLATFORMS, PLATFORM_KEYS, DEFAULT_WORKER_COUNT, SCRAPE_MODE_LABELS, isPlatform, platformConfig, type PlatformKey, type ScrapeMode } from '@/lib/platforms';
import {
  applyRunContext as applyRunContextPure,
  appendResult,
  applySessionSnapshot,
  applyStatus,
  clearLive,
  detectLogLevel,
  failedLinksForKind,
  groupDuplicateLocations,
  initialScanState,
  isNoStatsStatus,
  isStaleTerminalEvent,
  scanIsBusy,
  setDuplicates,
  shouldApplyRunContext,
  terminalLogLine,
  type LogDetails,
  type ProgressData,
  type RunContext,
  type ScanState,
  type SessionSnapshot,
  type SocketMessage,
} from '@/lib/scan';
import { dataSheetNameForSummaryTab, dataSheetsVisibleFirst, filterDataSheets, isSummarySheetName, summarySheetPlatform } from '@/lib/sheets';
import {
  emptySource,
  sourcePreferencesSnapshot,
  sourceQuery,
  validatedSourcePreferences,
  type SourcePreferences,
  type SourceSlot,
} from '@/lib/sources';
import type { MonthEntry, RdTable } from '@/lib/riviuData';
import { createStore, useStoreSelector } from './store';

// ---- Types ----------------------------------------------------------------------------

export type WorkspaceTab = 'sheet' | 'live' | 'success' | 'nostats' | 'errors' | 'duplicates' | 'logs';

export interface FileEntry {
  id: string;
  label?: string;
}

export interface LogEntry {
  id: number;
  time: string;
  level: string;
  message: string;
  details: LogDetails | null;
  /** The line as copied/exported: `[full time] [LEVEL] message`. */
  text: string;
}

export type SummaryView =
  | { state: 'loading' }
  | { state: 'ready'; title: string; partners: number; columns: string[]; rows: Array<Record<string, unknown>> }
  | { state: 'error'; text: string };

export type PreviewView =
  | { kind: 'loading' }
  | { kind: 'empty' }
  | { kind: 'message'; text: string; muted?: boolean }
  | { kind: 'table'; columns: string[]; rows: PreviewRow[]; platform: PlatformKey }
  | { kind: 'summary'; summary: SummaryView };

export interface CookieStatus {
  configured: boolean;
  state: string;
  generation?: string;
  count?: number;
  checkedAt?: string;
  message?: string;
}

export type CookieKind = 'threads' | 'tiktok';

export interface GoogleStatus {
  configured?: boolean;
  valid?: boolean;
  authorized?: boolean;
  ready?: boolean;
  accountEmail?: string;
  activeCredential?: string;
  serviceAccount?: { configured?: boolean; email?: string; error?: string };
}

export type ModalName = 'history' | 'report' | 'guide' | 'proxy' | 'threadsCookie' | 'tiktokCookie' | 'googlePaste' | 'schedule' | 'export';

/** Where posts and metrics live (GET /api/version "storage"). */
export type StorageKind = 'workbook' | 'postgres';

/** One platform's scan toolbar, persisted server side (/scan-settings). */
export interface ScanSettings {
  workers: number;
  scrapeMode: ScrapeMode;
  useProxy: boolean;
  useSession: boolean;
}

/** Riviu Data (Postgres storage): the month sheets per platform and the shown table. */
export interface RdState {
  months: Record<PlatformKey, MonthEntry[] | null>;
  thang: Record<PlatformKey, string>;
  table: RdTable;
}

export function defaultScanSettings(platform: PlatformKey): ScanSettings {
  return { workers: DEFAULT_WORKER_COUNT, scrapeMode: PLATFORMS[platform].defaultScrapeMode, useProxy: false, useSession: false };
}

export interface AppState {
  viewer: Viewer;
  storage: StorageKind;
  rd: RdState;
  scanSettings: Record<PlatformKey, ScanSettings>;
  activePlatform: PlatformKey;
  sources: Record<PlatformKey, SourceSlot>;
  files: FileEntry[];
  sourceBusy: boolean;
  sourcesInitialized: boolean;
  googleAuthorized: boolean;
  googleStatus: GoogleStatus | null;
  scrapeModes: Record<PlatformKey, ScrapeMode>;
  workers: number;
  useProxy: boolean;
  useThreadsSession: boolean;
  useTikTokSession: boolean;
  proxyCount: number;
  proxyText: string;
  workspaceTab: WorkspaceTab;
  /** The scan of the shown platform (every scan on workbook storage). */
  scan: ScanState;
  /**
   * Riviu Data storage scans TikTok and Threads side by side: the scan of each platform not shown
   * right now, kept up to date from the socket and swapped in when the admin switches platform.
   */
  parkedScans: Partial<Record<PlatformKey, ScanState>>;
  logs: LogEntry[];
  connection: '' | 'reconnecting' | 'lost';
  preview: PreviewView;
  workbookSheets: string[];
  /** Link count of the shown sheet ("Dữ liệu sheet" badge) while no scan runs. */
  sheetLinkCount: number;
  cookies: Record<CookieKind, CookieStatus>;
  cookieBusy: Record<CookieKind, boolean>;
  sourceDrawerOpen: boolean;
  settingsDrawerOpen: boolean;
  modal: ModalName | null;
  googlePasteUrl: string;
  /** Sheet the report modal opens on, and the partners a refresh scan targets. */
  reportSheet: string;
}

function initialState(): AppState {
  return {
    viewer: { authenticated: true, username: '', role: 'viewer', serverMode: true },
    storage: 'workbook',
    rd: {
      months: Object.fromEntries(PLATFORM_KEYS.map(key => [key, null])) as Record<PlatformKey, MonthEntry[] | null>,
      thang: Object.fromEntries(PLATFORM_KEYS.map(key => [key, ''])) as Record<PlatformKey, string>,
      table: { state: 'loading' },
    },
    scanSettings: Object.fromEntries(PLATFORM_KEYS.map(key => [key, defaultScanSettings(key)])) as Record<PlatformKey, ScanSettings>,
    activePlatform: PLATFORM_KEYS[0],
    sources: Object.fromEntries(PLATFORM_KEYS.map(key => [key, emptySource()])) as Record<PlatformKey, SourceSlot>,
    files: [],
    sourceBusy: false,
    sourcesInitialized: false,
    googleAuthorized: false,
    googleStatus: null,
    scrapeModes: Object.fromEntries(PLATFORM_KEYS.map(key => [key, PLATFORMS[key].defaultScrapeMode])) as Record<PlatformKey, ScrapeMode>,
    workers: DEFAULT_WORKER_COUNT,
    useProxy: false,
    useThreadsSession: false,
    useTikTokSession: false,
    proxyCount: 0,
    proxyText: '',
    workspaceTab: 'sheet',
    scan: initialScanState(),
    parkedScans: {},
    logs: [],
    connection: '',
    preview: { kind: 'loading' },
    workbookSheets: [],
    sheetLinkCount: 0,
    cookies: { threads: { configured: false, state: 'none', generation: '' }, tiktok: { configured: false, state: 'none', generation: '' } },
    cookieBusy: { threads: false, tiktok: false },
    sourceDrawerOpen: false,
    settingsDrawerOpen: false,
    modal: null,
    googlePasteUrl: '',
    reportSheet: '',
  };
}

export const appStore = createStore<AppState>(initialState());

export function useApp<S>(selector: (state: AppState) => S): S {
  return useStoreSelector(appStore, selector);
}

const get = () => appStore.get();
const set = appStore.set;

/** Test seam: reset everything (store and module counters). */
export function resetAppState(patch: Partial<AppState> = {}) {
  appStore.set({ ...initialState(), ...patch });
  for (const key of Object.keys(readSequence) as Array<keyof typeof readSequence>) readSequence[key] = 0;
  sourceRevision = 0;
  prefs.pending = null;
  prefs.ready = false;
  prefs.load = null;
  prefs.writing = false;
  prefs.failed = false;
  prefs.generation = 0;
  cookieStatusSeen.threads = false;
  cookieStatusSeen.tiktok = false;
}

// ---- Derived helpers ------------------------------------------------------------------

export function activeSource(state: AppState = get()): SourceSlot {
  return state.sources[state.activePlatform];
}

export function canAdmin(state: AppState = get()): boolean {
  return isAdmin(state.viewer);
}

export function uiIsBusy(state: AppState = get()): boolean {
  return scanIsBusy(state.scan.phase) || state.cookieBusy.threads || state.cookieBusy.tiktok || state.sourceBusy;
}

export const CONNECTION_RECONNECTING_TEXT = 'Đang kết nối lại tới server…';
export const CONNECTION_LOST_TEXT = 'Mất kết nối tới server. Ứng dụng vẫn tự thử lại, hoặc tải lại trang (F5).';

/** Run context `fileId` of a Riviu Data scan (scan_service.RIVIU_DATA_FILE_ID). */
export const RIVIU_DATA_FILE_ID = 'riviu-data';

/** What the page shows, for the stale-event rules of lib/scan.ts. */
export function viewContext(state: AppState = get()) {
  return { activePlatform: state.activePlatform, currentFileId: state.storage === 'postgres' ? RIVIU_DATA_FILE_ID : activeSource(state).fileId };
}

/** Why an idle Start button cannot start a scan right now ('' when it can). */
export function startBlockedReason(state: AppState = get()): string {
  if (state.connection === 'lost') return CONNECTION_LOST_TEXT;
  if (state.connection === 'reconnecting') return CONNECTION_RECONNECTING_TEXT;
  if (state.storage === 'postgres') return state.rd.thang[state.activePlatform] ? '' : 'Chưa có tháng nào để quét.';
  if (state.sourcesInitialized && !activeSource(state).fileId) return 'Chưa chọn nguồn cho nền tảng này.';
  return '';
}

/** The only rule for a "Tạo sheet" button: idle UI, Google login, a target URL, that platform's own file and sheet. */
export function googlePushEnabled(state: AppState, platform: PlatformKey): boolean {
  const source = state.sources[platform];
  return Boolean(canAdmin(state) && !uiIsBusy(state) && state.googleAuthorized && source.url.trim() && source.fileId && (source.pushSheet || source.scanSheet));
}

function updateSource(platform: PlatformKey, patch: Partial<SourceSlot> | ((slot: SourceSlot) => Partial<SourceSlot>)) {
  set(state => {
    const slot = state.sources[platform];
    const next = typeof patch === 'function' ? patch(slot) : patch;
    return { sources: { ...state.sources, [platform]: { ...slot, ...next } } };
  });
}

// ---- Logs and notifications -----------------------------------------------------------

const MAX_LOG_LINES = 5000;
let logSeq = 0;

export function addLog(message: string, options: { level?: string; details?: LogDetails | null } = {}) {
  const now = new Date();
  const level = detectLogLevel(message, options.level);
  logSeq += 1;
  const entry: LogEntry = {
    id: logSeq,
    time: now.toLocaleTimeString('vi-VN'),
    level,
    message,
    details: options.details || null,
    text: `[${now.toLocaleString('vi-VN')}] [${level}] ${message}`,
  };
  set(state => ({ logs: state.logs.length >= MAX_LOG_LINES ? [...state.logs.slice(-MAX_LOG_LINES + 1), entry] : [...state.logs, entry] }));
}

export type NotifyLevel = 'success' | 'error' | 'warn' | 'info';

export function notify(message: string, level: NotifyLevel = 'info') {
  const logLevel = level === 'warn' ? 'WARN' : level === 'success' ? 'OK' : level === 'error' ? 'ERROR' : '';
  addLog(message, { level: logLevel });
  if (level === 'success') toast.success(message);
  else if (level === 'error') toast.error(message);
  else if (level === 'warn') toast.warning(message);
  else toast.info(message);
}

export function clearLogs() {
  set({ logs: [] });
  addLog('Đã xóa log hệ thống.');
}

export async function copyLogs() {
  const text = get().logs.map(line => line.text).join('\n');
  if (!text) { addLog('Không có log để copy.'); return; }
  try {
    await copyText(text);
    addLog('Đã copy toàn bộ log hệ thống.');
  } catch (error) {
    addLog(`Lỗi copy log: ${errorMessage(error, '')}`);
  }
}

export function exportLogs() {
  const text = get().logs.map(line => line.text).join('\n');
  if (!text) { addLog('Không có log để xuất.'); return; }
  const filename = `log-${fileTimestamp()}.txt`;
  try {
    downloadBlob(new Blob([text], { type: 'text/plain;charset=utf-8' }), filename);
    addLog(`Đã xuất log ra file ${filename}.`, { level: 'OK' });
  } catch (error) {
    addLog(`Lỗi xuất log: ${errorMessage(error, '')}`);
  }
}

// ---- Layout ---------------------------------------------------------------------------

export function setWorkspaceTab(tab: WorkspaceTab) {
  set({ workspaceTab: tab });
}

export function openModal(modal: ModalName) {
  set({ modal, sourceDrawerOpen: false, settingsDrawerOpen: false });
}

export function closeModal() {
  set({ modal: null });
}

export function setSourceDrawer(open: boolean) {
  set({ sourceDrawerOpen: open, settingsDrawerOpen: false });
}

export function setSettingsDrawer(open: boolean) {
  set({ settingsDrawerOpen: open, sourceDrawerOpen: false });
}

// ---- Read-request guards --------------------------------------------------------------

const readSequence = { files: 0, preview: 0, summary: 0 };
let sourceRevision = 0;

/** A guard that is true only while the source, platform (and sheet) of the request are current. */
function beginReadRequest(kind: keyof typeof readSequence, trackSheet = false) {
  const sequence = ++readSequence[kind];
  const state = get();
  const fileId = activeSource(state).fileId;
  const platform = state.activePlatform;
  const displaySheet = activeSource(state).displaySheet;
  const revision = sourceRevision;
  return (data: { file?: unknown; current?: unknown } = {}) => {
    const now = get();
    const responseFile = (data.file || data.current) as string | undefined;
    return revision === sourceRevision && sequence === readSequence[kind]
      && fileId === activeSource(now).fileId && platform === now.activePlatform
      && (!trackSheet || displaySheet === activeSource(now).displaySheet)
      && (!fileId || !responseFile || responseFile === fileId);
  };
}

// ---- Source preferences ---------------------------------------------------------------

const SOURCE_PREFERENCES_FALLBACK_KEY = 'riviuPlatformSourcesV1';
/** Viewers keep their own file/sheet choice in this browser; they never write the team's preferences. */
const VIEWER_SOURCES_KEY = 'riviuViewerSourcesV1';

const prefs = {
  load: null as Promise<void> | null,
  ready: false,
  generation: 0,
  pending: null as { sources: SourcePreferences } | null,
  writing: false,
  failed: false,
  scheduled: false,
};

function readLocal(key: string): unknown {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; }
}

function writeLocal(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}

function removeLocal(key: string) {
  try { localStorage.removeItem(key); } catch { /* storage unavailable */ }
}

export function loadSourcePreferences(): Promise<void> {
  if (prefs.load) return prefs.load;
  const generation = prefs.generation;
  prefs.load = (async () => {
    let sources: SourcePreferences | undefined;
    if (!canAdmin()) {
      try { sources = validatedSourcePreferences(readLocal(VIEWER_SOURCES_KEY)); } catch { sources = undefined; }
    }
    if (!sources) {
      try {
        const { response, data } = await getJson<{ sources?: unknown; error?: string }>('/source-preferences', { cache: 'no-store', quietForbidden: true });
        if (!response.ok || data.error) throw new Error(data.error || 'Không tải được nguồn đã lưu.');
        sources = validatedSourcePreferences(data.sources);
      } catch {
        prefs.failed = true;
        try { sources = validatedSourcePreferences(readLocal(SOURCE_PREFERENCES_FALLBACK_KEY)); } catch { sources = undefined; }
      }
    }
    if (sources && generation === prefs.generation) {
      const loaded = sources;
      set(state => ({
        sources: Object.fromEntries(PLATFORM_KEYS.map(platform => [platform, { ...state.sources[platform], ...loaded[platform] }])) as Record<PlatformKey, SourceSlot>,
      }));
    }
    prefs.ready = true;
    scheduleSourcePreferencesSave();
  })();
  return prefs.load;
}

export function scheduleSourcePreferencesSave() {
  const state = get();
  if (!prefs.ready || !prefs.pending || prefs.scheduled || prefs.writing || state.sourceBusy || scanIsBusy(state.scan.phase)) return;
  prefs.scheduled = true;
  void Promise.resolve().then(() => {
    prefs.scheduled = false;
    void flushSourcePreferences();
  });
}

async function flushSourcePreferences() {
  const busy = () => get().sourceBusy || scanIsBusy(get().scan.phase);
  if (!prefs.ready || prefs.writing || !prefs.pending || busy()) return;
  prefs.writing = true;
  try {
    while (prefs.pending && !busy()) {
      const pending: { sources: SourcePreferences } = prefs.pending;
      prefs.pending = null;
      try {
        const { response, data } = await postJson<{ success?: boolean; error?: string }>('/source-preferences', { sources: pending.sources }, { quietForbidden: true });
        if (response.status === 409) {
          prefs.pending ||= pending;
          break; // Retry on the next idle state change, not while the backend is locked.
        }
        if (!response.ok || !data.success) throw new Error(data.error || 'Không lưu được nguồn dữ liệu.');
        prefs.failed = false;
        removeLocal(SOURCE_PREFERENCES_FALLBACK_KEY);
      } catch {
        prefs.failed = true;
        writeLocal(SOURCE_PREFERENCES_FALLBACK_KEY, prefs.pending?.sources || pending.sources);
      }
    }
  } finally {
    prefs.writing = false;
  }
}

export function persistSources() {
  prefs.generation += 1;
  let sources: SourcePreferences;
  try { sources = sourcePreferencesSnapshot(get().sources); } catch { return; }
  if (!canAdmin()) {
    writeLocal(VIEWER_SOURCES_KEY, sources);
    return;
  }
  prefs.pending = { sources };
  if (prefs.failed) writeLocal(SOURCE_PREFERENCES_FALLBACK_KEY, sources);
  scheduleSourcePreferencesSave();
}

// Busy gates reopening flush any preference save that waited for them.
appStore.subscribe(() => {
  if (prefs.pending && !prefs.writing && !prefs.scheduled) scheduleSourcePreferencesSave();
});

// ---- Sources --------------------------------------------------------------------------

function setSourceBusy(busy: boolean) {
  set({ sourceBusy: busy });
}

interface FileListData {
  files?: FileEntry[];
  current?: string;
  currentLabel?: string;
  currentSheet?: string;
  sheets?: string[];
  hiddenSheets?: string[];
  scanSheet?: string;
  googleSheetUrl?: string;
  googleOAuthAuthorized?: boolean;
  error?: string;
}

export async function fetchFileList(platform: PlatformKey, fileId: string) {
  return getJson<FileListData>(`/list-files?${sourceQuery(platform, fileId)}`);
}

async function selectFileOnServer(platform: PlatformKey, fileId: string, sheetName: string, scanSheet: string) {
  return postJson<{ success?: boolean; error?: string; sheet?: string; scanSheet?: string; selected?: string }>(
    '/select-file', { filename: fileId, file_id: fileId, platform, sheet_name: sheetName, scan_sheet: scanSheet },
  );
}

/**
 * A saved sheet name may no longer exist (renamed/deleted tab, re-synced Google Sheet). Never let
 * it block the workbook: drop names the known sheet list lacks, and if the server still rejects
 * the sheets, retry once without them. Viewers resolve the sheets locally and never POST.
 */
export async function activateSource(platform: PlatformKey) {
  let source = get().sources[platform];
  if (!source.fileId) return;
  if (!source.sheets.length) {
    try {
      const { response, data } = await fetchFileList(platform, source.fileId);
      if (response.ok && data.current === source.fileId && Array.isArray(data.sheets)) {
        updateSource(platform, slot => ({ sheets: data.sheets || [], hiddenSheets: data.hiddenSheets || [], label: slot.label || data.currentLabel || '' }));
        source = get().sources[platform];
      }
    } catch { /* keep the saved sheets */ }
  }
  const known = source.sheets;
  const keep = (sheet: string) => (sheet && (!known.length || known.includes(sheet)) ? sheet : '');
  const sheetName = keep(source.displaySheet);
  const scanSheet = keep(source.scanSheet);
  if (!canAdmin()) {
    const dataSheets = dataSheetsVisibleFirst(known, source.hiddenSheets);
    const display = sheetName || dataSheets[0] || '';
    const scan = filterDataSheets([scanSheet]).length ? scanSheet : display;
    updateSource(platform, { displaySheet: display, scanSheet: scan, pushSheet: scan });
    persistSources();
    return;
  }
  let { response, data } = await selectFileOnServer(platform, source.fileId, sheetName, scanSheet);
  if (response.status === 400 && (sheetName || scanSheet)) ({ response, data } = await selectFileOnServer(platform, source.fileId, '', ''));
  if (!response.ok || !data.success) throw new Error(data.error || 'Không chọn được workbook.');
  updateSource(platform, slot => {
    const displaySheet = data.sheet || '';
    const nextScan = data.scanSheet || displaySheet;
    return { displaySheet, scanSheet: nextScan, pushSheet: slot.sheets.includes(slot.pushSheet) ? slot.pushSheet : nextScan };
  });
  persistSources();
}

/** First load: reopen only the workbook this platform saved; anything else leaves the empty state. */
async function initializeSources(applyGoogleSheetUrl: boolean) {
  const isCurrent = beginReadRequest('files');
  set({ sourcesInitialized: true });
  const platform = get().activePlatform;
  const saved = get().sources[platform];
  if (saved.fileId) {
    setSourceBusy(true);
    try {
      const { response, data } = await fetchFileList(platform, saved.fileId);
      if (!isCurrent(data)) return;
      if (!response.ok || data.error || data.current !== saved.fileId) throw new Error(data.error || 'Không tải được workbook đã lưu.');
      updateSource(platform, { sheets: data.sheets || [], hiddenSheets: data.hiddenSheets || [], label: data.currentLabel || saved.fileId });
      try { await activateSource(platform); } catch (error) {
        if (!isCurrent()) return;
        addLog(`Không chọn lại được sheet đã lưu: ${errorMessage(error, '')}`);
      }
    } catch (error) {
      if (!isCurrent()) return;
      updateSource(platform, slot => ({ ...emptySource(), url: slot.url }));
      persistSources();
      addLog(`Không mở lại được nguồn đã lưu: ${errorMessage(error, '')}`);
    } finally {
      setSourceBusy(false);
    }
  }
  await updateFileList({ applyGoogleSheetUrl: applyGoogleSheetUrl && !get().sources[platform].url });
}

function setGoogleSheetUrlField(url: string | undefined, force = false) {
  const normalized = String(url || '').trim();
  const platform = get().activePlatform;
  const source = get().sources[platform];
  if (force || !source.dirty) updateSource(platform, { url: normalized, ...(force ? { dirty: false } : {}) });
}

function applyFileListState(data: FileListData, applyGoogleSheetUrl: boolean) {
  if (applyGoogleSheetUrl) setGoogleSheetUrlField(data.googleSheetUrl);
  const platform = get().activePlatform;
  updateSource(platform, source => {
    const sheets = data.sheets || [];
    const next: Partial<SourceSlot> = {
      sheets,
      hiddenSheets: data.hiddenSheets || [],
      label: source.fileId ? (data.currentLabel || source.fileId) : '',
    };
    let { displaySheet, scanSheet, pushSheet } = source;
    if (!sheets.includes(displaySheet)) displaySheet = data.currentSheet || data.scanSheet || '';
    if (!sheets.includes(scanSheet)) scanSheet = data.scanSheet || data.currentSheet || '';
    const dataSheets = dataSheetsVisibleFirst(sheets, data.hiddenSheets || []);
    if (!dataSheets.includes(scanSheet)) scanSheet = dataSheets[0] || '';
    pushSheet ||= scanSheet;
    if (!dataSheets.includes(pushSheet)) pushSheet = scanSheet;
    return { ...next, displaySheet, scanSheet, pushSheet };
  });
  set({ googleAuthorized: Boolean(data.googleOAuthAuthorized) });
}

export async function updateFileList({ applyGoogleSheetUrl = false } = {}): Promise<void> {
  if (!get().sourcesInitialized) return initializeSources(applyGoogleSheetUrl);
  const isCurrent = beginReadRequest('files');
  try {
    const platform = get().activePlatform;
    const { response, data } = await fetchFileList(platform, activeSource().fileId);
    if (!isCurrent(data)) return;
    if (!response.ok || data.error) throw new Error(data.error || 'Không tải được nguồn dữ liệu.');
    applyFileListState(data, applyGoogleSheetUrl);
    set({ files: data.files || [] });
    if ((data.files || []).length && canAdmin()) void refreshGoogleStatus();
    persistSources();
  } catch (error) {
    if (!isCurrent()) return;
    addLog(`Lỗi tải danh sách file: ${errorMessage(error, '')}`);
  } finally {
    const scan = get().scan;
    if (scanIsBusy(scan.phase) && scan.runContext) showRunSource(scan.runContext);
  }
}

export async function selectFile(fileId: string) {
  if (!fileId || uiIsBusy()) return;
  const platform = get().activePlatform;
  setSourceBusy(true);
  ++sourceRevision;
  try {
    if (canAdmin()) {
      updateSource(platform, { displaySheet: '' });
      const { data } = await postJson<{ success?: boolean; error?: string; selected?: string; sheet?: string; scanSheet?: string }>('/select-file', { filename: fileId, file_id: fileId, platform });
      if (!data.success) throw new Error(data.error || 'Không chọn được file');
      const displaySheet = data.sheet || '';
      const scanSheet = data.scanSheet || displaySheet;
      updateSource(platform, { fileId: data.selected || fileId, displaySheet, scanSheet, pushSheet: scanSheet, dirty: false, sheets: [] });
    } else {
      updateSource(platform, { fileId, displaySheet: '', scanSheet: '', pushSheet: '', sheets: [], hiddenSheets: [], dirty: false });
    }
    setSourceBusy(false);
    await updateFileList({ applyGoogleSheetUrl: true });
    await loadPreview();
    addLog(`Đã chuyển sang file: ${get().sources[platform].label || fileId}`);
  } catch (error) {
    addLog(`Lỗi: ${errorMessage(error, '')}`);
  } finally {
    setSourceBusy(false);
  }
}

export async function setPlatform(platform: PlatformKey) {
  const state = get();
  if (!isPlatform(platform) || uiIsBusy(state) || state.activePlatform === platform) return;
  persistSources();
  setSourceBusy(true);
  ++sourceRevision;
  try {
    if (get().sources[platform].fileId) await activateSource(platform);
    set({ activePlatform: platform });
  } catch (error) {
    notify(errorMessage(error, 'Không chuyển được nguồn dữ liệu.'), 'error');
    return;
  } finally {
    setSourceBusy(false);
  }
  set(s => ({ scan: { ...clearLive(s.scan), phase: scanIsBusy(s.scan.phase) ? s.scan.phase : 'idle' }, workspaceTab: 'sheet' }));
  const source = activeSource();
  if (isSummarySheetName(source.displaySheet) && summarySheetPlatform(source.displaySheet) !== platform) {
    updateSource(platform, { displaySheet: dataSheetsVisibleFirst(get().workbookSheets, source.hiddenSheets)[0] || '' });
  }
  if (get().sources[platform].fileId && !get().sources[platform].sheets.length) await updateFileList();
  void loadPreview();
}

export function setScanSheet(sheet: string) {
  updateSource(get().activePlatform, { scanSheet: sheet });
  persistSources();
}

/** Source-row sheet select: one choice drives the push, scan and shown sheet of that platform. */
export function setSourceRowSheet(platform: PlatformKey, sheet: string) {
  updateSource(platform, { pushSheet: sheet, scanSheet: sheet, displaySheet: sheet });
  if (platform === get().activePlatform) void loadPreview(sheet);
  persistSources();
}

export function setSourceUrl(platform: PlatformKey, url: string) {
  updateSource(platform, { url, dirty: true });
  persistSources();
}

export async function switchSheet(sheetName: string) {
  const state = get();
  if (state.sourceBusy || scanIsBusy(state.scan.phase)) return;
  updateSource(state.activePlatform, { displaySheet: sheetName });
  persistSources();
  await loadPreview(sheetName);
}

export async function syncGoogleSheet(platform: PlatformKey) {
  if (!canAdmin() || !isPlatform(platform) || uiIsBusy()) return;
  const url = get().sources[platform].url.trim();
  if (!url) { addLog('Vui lòng nhập URL Google Sheet.'); return; }
  setSourceBusy(true);
  const revision = ++sourceRevision;
  try {
    const { response, data } = await postJson<{ error?: string; file?: string; label?: string; currentSheet?: string; scanSheet?: string; sheets?: string[]; hiddenSheets?: string[] }>(
      '/sync-google-sheet', { url, platform, activate: false },
    );
    if (!response.ok) throw new Error(data.error || 'Không đồng bộ được Google Sheet');
    if (revision !== sourceRevision) return;
    const scanSheet = data.scanSheet || data.currentSheet || '';
    updateSource(platform, {
      fileId: data.file || '', label: data.label || data.file || '', displaySheet: data.currentSheet || '', scanSheet, pushSheet: scanSheet,
      sheets: data.sheets || [], hiddenSheets: data.hiddenSheets || [], url, dirty: false,
    });
    setSourceBusy(false);
    if (platform === get().activePlatform) {
      await activateSource(platform);
      await updateFileList();
      await loadPreview();
    }
    persistSources();
    notify(`Đã nạp Google Sheet: ${data.label || data.file || ''}`, 'success');
  } catch (error) {
    notify(`Lỗi đồng bộ Google Sheet: ${errorMessage(error, '')}`, 'error');
  } finally {
    setSourceBusy(false);
  }
}

export async function uploadFile(file: File) {
  if (!canAdmin() || uiIsBusy()) return;
  const platform = get().activePlatform;
  setSourceBusy(true);
  ++sourceRevision;
  addLog(`Đang tải lên: ${file.name}...`);
  const form = new FormData();
  form.append('file', file);
  form.append('platform', platform);
  form.append('activate', 'false');
  try {
    const { data } = await postForm<{ success?: boolean; error?: string; filename?: string; sheet?: string; scanSheet?: string; sheets?: string[]; hiddenSheets?: string[] }>('/upload-excel', form);
    if (!data.success) throw new Error(data.error || 'Tải file thất bại');
    const displaySheet = data.sheet || '';
    const scanSheet = data.scanSheet || displaySheet;
    updateSource(platform, { fileId: data.filename || '', displaySheet, scanSheet, pushSheet: scanSheet, sheets: data.sheets || [], hiddenSheets: data.hiddenSheets || [], url: '', dirty: false });
    setSourceBusy(false);
    await activateSource(platform);
    await updateFileList({ applyGoogleSheetUrl: true });
    await loadPreview();
    notify(`Tải lên thành công: ${file.name}`, 'success');
  } catch (error) {
    notify(`Lỗi tải file: ${errorMessage(error, '')}`, 'error');
  } finally {
    setSourceBusy(false);
  }
}

export async function pushToGoogle(platform: PlatformKey) {
  if (!canAdmin() || uiIsBusy()) return;
  persistSources();
  const source = get().sources[platform];
  if (!source.fileId) { notify('Chưa chọn file cho nền tảng này.', 'warn'); return; }
  if (!googlePushEnabled(get(), platform)) {
    addLog('Nút Tạo sheet cần đăng nhập Google, URL sheet đích, file local và sheet nguồn.');
    return;
  }
  const url = source.url.trim();
  const sourceSheet = source.pushSheet || source.scanSheet;
  setSourceBusy(true);
  try {
    const { response, data } = await postJson<{ success?: boolean; error?: string; sheetTitle?: string; sourceSheet?: string }>(
      '/push-google-sheet', { url, sourceSheet, platform, file_id: source.fileId },
    );
    if (!response.ok || !data.success) throw new Error(data.error || 'Không tạo được sheet trên Google');
    notify(`Đã tạo sheet Google mới từ ${data.sourceSheet || sourceSheet}: ${data.sheetTitle}`, 'success');
  } catch (error) {
    notify(`Lỗi tạo sheet Google: ${errorMessage(error, '')}`, 'error');
  } finally {
    setSourceBusy(false);
  }
}

export function workbookDownloadUrl(state: AppState = get()): string {
  return `/download-excel?${sourceQuery(state.activePlatform, activeSource(state).fileId)}`;
}

export function downloadCurrentWorkbook() {
  const state = get();
  if (!canAdmin(state) || !activeSource(state).fileId || state.sourceBusy) return;
  const link = document.createElement('a');
  link.href = workbookDownloadUrl(state);
  link.click();
}

// ---- Preview and summary --------------------------------------------------------------

interface PreviewData {
  file?: string;
  fileLabel?: string;
  sheets?: string[];
  hiddenSheets?: string[];
  currentSheet?: string;
  columns?: string[];
  data?: PreviewRow[];
  message?: string;
  error?: string;
}

export async function loadPreview(sheetName = ''): Promise<void> {
  const state = get();
  if (state.sourcesInitialized && !activeSource(state).fileId) {
    ++readSequence.preview;
    ++readSequence.summary;
    set({ preview: { kind: 'empty' }, workbookSheets: [], sheetLinkCount: 0 });
    return;
  }
  const platform = state.activePlatform;
  const requestedSheet = sheetName || activeSource(state).displaySheet;
  const isCurrent = beginReadRequest('preview', true);
  if (get().preview.kind !== 'table') set({ preview: { kind: 'loading' } });
  try {
    const { data } = await getJson<PreviewData>(`/preview-excel?${sourceQuery(platform, activeSource().fileId, { sheet_name: requestedSheet })}`);
    if (!isCurrent(data)) return;
    if (data.error || data.message) {
      set({ preview: { kind: 'message', text: data.message || data.error || '' } });
      return;
    }
    const sheets = data.sheets || [];
    updateSource(platform, slot => ({
      displaySheet: data.currentSheet || '',
      hiddenSheets: data.hiddenSheets || [],
      sheets,
      label: data.fileLabel || slot.fileId,
      scanSheet: dataSheetsVisibleFirst(sheets, data.hiddenSheets || []).includes(slot.scanSheet)
        ? slot.scanSheet
        : dataSheetsVisibleFirst(sheets, data.hiddenSheets || [])[0] || '',
    }));
    set({ workbookSheets: sheets });
    persistSources();
    const shown = activeSource().displaySheet;
    if (isSummarySheetName(shown)) {
      // Ask for the opened tab itself: a data sheet can have one summary tab per platform.
      await loadSummary(shown);
      return;
    }
    if (!data.columns || data.columns.length === 0) {
      set({ preview: { kind: 'message', text: 'Sheet này chưa có dữ liệu.', muted: true } });
      return;
    }
    const preview = platformPreview(data, platform);
    set({ preview: { kind: 'table', columns: preview.columns, rows: preview.rows, platform }, sheetLinkCount: preview.linkCount });
  } catch (error) {
    if (!isCurrent()) return;
    addLog(`Lỗi preview: ${errorMessage(error, '')}`);
  }
}

export const DEFAULT_SUMMARY_COLUMNS = ['Stt', 'ĐỐI TÁC', 'TỔNG LINK', 'TỔNG LƯỢT XEM', 'TỔNG TIM', 'TỔNG BÌNH LUẬN', 'TỔNG LƯỢT LƯU', 'TỔNG CHIA SẺ', 'Cập nhật lần cuối'];

async function loadSummary(dataSheetName: string) {
  const state = get();
  if (state.sourcesInitialized && !activeSource(state).fileId) return;
  const isCurrent = beginReadRequest('summary', true);
  set({ preview: { kind: 'summary', summary: { state: 'loading' } } });
  const source = activeSource(state);
  const resolved = dataSheetName
    || dataSheetNameForSummaryTab(source.displaySheet, state.workbookSheets)
    || source.scanSheet
    || dataSheetsVisibleFirst(state.workbookSheets, source.hiddenSheets)[0]
    || '';
  try {
    const { response, data } = await getJson<{ error?: string; sheet?: string; columns?: string[]; rows?: Array<Record<string, unknown>>; totals?: { partners?: number }; file?: string }>(
      `/summary-dashboard?${sourceQuery(state.activePlatform, source.fileId, { sheet_name: resolved })}`,
    );
    if (!isCurrent(data)) return;
    if (!response.ok) throw new Error(data.error || 'Không đọc được sheet Tổng kết');
    set({
      preview: {
        kind: 'summary',
        summary: {
          state: 'ready',
          title: String(data.sheet || 'Tổng kết').trim(),
          partners: Number(data.totals?.partners || 0),
          columns: data.columns && data.columns.length ? data.columns : DEFAULT_SUMMARY_COLUMNS,
          rows: data.rows || [],
        },
      },
    });
  } catch (error) {
    if (!isCurrent()) return;
    set({ preview: { kind: 'summary', summary: { state: 'error', text: errorMessage(error, '') } } });
    addLog(`Lỗi tổng kết: ${errorMessage(error, '')}`);
  }
}

// ---- Run context and the socket -------------------------------------------------------

/**
 * Storage-specific reactions to the socket, set by state/riviuData.ts on Postgres storage:
 * follow a run's platform/month, reload the table once a run's numbers were saved.
 */
export const storageHooks: {
  followRun?: (context: RunContext) => void;
  numbersSaved?: (context: RunContext | null, finished: boolean) => void;
} = {};

function applyRunContext(context: RunContext) {
  if (!context?.runId) return;
  set(state => ({ scan: applyRunContextPure(state.scan, context).state }));
  showRunSource(context);
}

/**
 * Follow the workbook the server is scanning. Every change goes through that platform's own
 * source slot, and a platform/file change reloads the label, sheet list and preview.
 */
function showRunSource(context: RunContext) {
  if (get().storage === 'postgres') {
    storageHooks.followRun?.(context);
    return;
  }
  const { platform, fileId, sheetName } = context;
  const state = get();
  const runPlatform = isPlatform(platform) ? platform : state.activePlatform;
  const slot = state.sources[runPlatform];
  const platformChanged = runPlatform !== state.activePlatform;
  const fileChanged = Boolean(fileId) && slot.fileId !== fileId;
  if (platformChanged) set({ activePlatform: runPlatform });
  if (fileChanged) {
    updateSource(runPlatform, { fileId: fileId || '', label: fileId || '', sheets: sheetName ? [sheetName] : [], hiddenSheets: [], displaySheet: sheetName || '', pushSheet: sheetName || '' });
  }
  if (sheetName) updateSource(runPlatform, { scanSheet: sheetName });
  if (platformChanged || fileChanged) {
    ++sourceRevision;
    persistSources();
    void (async () => { await updateFileList(); await loadPreview(); })();
  }
}

function finishScanRun(phase: string, stopReason: string) {
  addLog(terminalLogLine(phase as ScanState['phase'], stopReason));
  if (get().storage === 'postgres') {
    storageHooks.numbersSaved?.(get().scan.runContext, true);
    return;
  }
  void updateFileList();
  void loadPreview();
}

export function handleSessionSnapshot(session: SessionSnapshot) {
  const state = get();
  const { state: scan, follow } = applySessionSnapshot(state.scan, session, viewContext(state));
  set({ scan });
  if (follow) showRunSource(session);
  if (session.running) set({ workspaceTab: 'live' });
}

/** The platform lane a socket event belongs to when it is not the shown platform (Riviu Data storage). */
function parkedLaneOf(message: SocketMessage, state: AppState = get()): PlatformKey | null {
  if (state.storage !== 'postgres') return null;
  const platform = message.type === 'session' ? (message.data as SessionSnapshot | undefined)?.platform ?? message.platform : message.platform;
  return platform && isPlatform(platform) && platform !== state.activePlatform ? platform : null;
}

/** The same reducers as the shown scan, applied to another platform's parked scan. */
function handleParkedMessage(platform: PlatformKey, message: SocketMessage) {
  const label = PLATFORMS[platform].label;
  let scan = get().parkedScans[platform] ?? initialScanState();
  if (message.type === 'session') {
    scan = applySessionSnapshot(scan, (message.data || {}) as SessionSnapshot, { activePlatform: platform, currentFileId: RIVIU_DATA_FILE_ID }).state;
  } else {
    if (shouldApplyRunContext(message)) scan = applyRunContextPure(scan, message).state;
    if (message.type === 'log') {
      addLog(`[${label}] ${message.message || ''}`, { level: message.level || '', details: message.details || null });
    } else if (message.type === 'status') {
      const { state: next, finished } = applyStatus(scan, (message.data || {}) as ProgressData);
      scan = next;
      if (finished) {
        addLog(`[${label}] ${terminalLogLine(finished.phase as ScanState['phase'], finished.stopReason)}`);
        storageHooks.numbersSaved?.(scan.runContext, true);
      }
    } else if (message.type === 'data' && message.row) {
      scan = appendResult(scan, message.row, message, platform);
    } else if (message.type === 'duplicates') {
      scan = setDuplicates(scan, message.data);
    }
  }
  set(state => ({ parkedScans: { ...state.parkedScans, [platform]: scan } }));
}

/** Show `platform`'s scan and park the one shown so far (Riviu Data storage). */
export function swapShownScan(platform: PlatformKey) {
  set(state => {
    if (platform === state.activePlatform) return {};
    const parkedScans = { ...state.parkedScans, [state.activePlatform]: state.scan };
    const scan = parkedScans[platform] ?? initialScanState();
    delete parkedScans[platform];
    return { activePlatform: platform, scan, parkedScans };
  });
}

/** A platform's scan is busy, whether it is shown or parked. */
export function platformScanBusy(state: AppState, platform: PlatformKey): boolean {
  const scan = platform === state.activePlatform ? state.scan : state.parkedScans[platform];
  return Boolean(scan && scanIsBusy(scan.phase));
}

export function handleSocketMessage(message: SocketMessage) {
  const parked = parkedLaneOf(message);
  if (parked) {
    handleParkedMessage(parked, message);
    return;
  }
  if (message.type === 'session') {
    handleSessionSnapshot((message.data || {}) as SessionSnapshot);
    return;
  }
  const state = get();
  if (isStaleTerminalEvent(message, viewContext(state), state.scan.runContext?.runId || '')) return;
  if (shouldApplyRunContext(message)) applyRunContext(message);
  if (message.type === 'log') {
    addLog(message.message || '', { level: message.level || '', details: message.details || null });
  } else if (message.type === 'status') {
    const data = (message.data || {}) as ProgressData;
    const previousWait = get().scan.retryWait;
    const { state: scan, finished } = applyStatus(get().scan, data);
    set({ scan });
    if (finished) finishScanRun(finished.phase, finished.stopReason);
    // The main pass is saved before the cooldown: its numbers are already in the table.
    else if (scan.retryWait && !previousWait && get().storage === 'postgres') storageHooks.numbersSaved?.(scan.runContext, false);
    // The cookie vault is Threads-only; a Threads scan may mark it expired.
    if (data.done && get().activePlatform === 'threads' && canAdmin()) void refreshCookieStatus('threads');
  } else if (message.type === 'data' && message.row) {
    set(s => ({ scan: appendResult(s.scan, message.row!, message, s.activePlatform) }));
  } else if (message.type === 'duplicates') {
    set(s => ({ scan: setDuplicates(s.scan, message.data) }));
  }
}

const WS_RECONNECT_BASE_MS = 1000;
const WS_RECONNECT_MAX_MS = 5000;
const WS_LOST_AFTER_MS = 15000;
let ws: WebSocket | null = null;
let wsFailures = 0;
let wsDisconnectedAt = 0;
let wsRetryPending = false;

export function reconnectDelay(failures: number): number {
  return Math.min(WS_RECONNECT_BASE_MS * 2 ** Math.max(failures - 1, 0), WS_RECONNECT_MAX_MS);
}

export function connectWS() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
  ws = socket;
  socket.onmessage = event => {
    try { handleSocketMessage(JSON.parse(event.data)); } catch (error) { console.error(error); }
  };
  socket.onopen = () => {
    wsFailures = 0;
    set({ connection: '' });
    addLog('Hệ thống đã kết nối trực tiếp.');
  };
  socket.onclose = () => {
    if (ws === socket) scheduleReconnect();
  };
}

/**
 * After a server restart the old session cookie is rejected, so /ws keeps closing until / is
 * loaded again. Every failure probes / first and reconnects as soon as the probe answers.
 */
function scheduleReconnect() {
  if (wsRetryPending) return;
  wsFailures += 1;
  if (wsFailures === 1) {
    wsDisconnectedAt = Date.now();
    addLog('Mất kết nối. Đang tự động kết nối lại...');
  }
  set({ connection: Date.now() - wsDisconnectedAt >= WS_LOST_AFTER_MS ? 'lost' : 'reconnecting' });
  wsRetryPending = true;
  if (wsFailures === 1) void probeSession();
  else setTimeout(() => void probeSession(), reconnectDelay(wsFailures - 1));
}

async function probeSession() {
  const reachable = await bootstrapLocalSession();
  wsRetryPending = false;
  if (reachable) connectWS();
  else scheduleReconnect();
}

// ---- Scan start and cancel ------------------------------------------------------------

export interface ScanRequest {
  logLine: string;
  payload: Record<string, unknown>;
}

/** Collect the scan settings, or explain (and return null) when the scan cannot start. */
export function buildScanRequest(state: AppState, partners: string[] = [], sheetName = ''): { request: ScanRequest | null; problem?: { message: string; open?: ModalName } } {
  const platform = state.activePlatform;
  const config = platformConfig(platform);
  const source = activeSource(state);
  const workers = Number(state.workers || DEFAULT_WORKER_COUNT);
  const scrapeMode = state.scrapeModes[platform] || 'request';
  const proxyText = state.useProxy ? state.proxyText : '';
  const threads = state.cookies.threads;
  const useThreadsSession = config.cookieSession && state.useThreadsSession;
  if (useThreadsSession && (!threads.configured || !threads.generation)) {
    return { request: null, problem: { message: 'Import và kiểm tra cookie Threads trước khi dùng phiên.', open: 'threadsCookie' } };
  }
  const useTikTokSession = config.accountCookie && state.useTikTokSession;
  if (useTikTokSession && !state.cookies.tiktok.configured) {
    return { request: null, problem: { message: 'Import và kiểm tra cookie TikTok trước khi dùng phiên.', open: 'tiktokCookie' } };
  }
  if (state.useProxy && !proxyText.trim()) {
    return { request: null, problem: { message: 'Bật proxy nhưng chưa có danh sách. Bấm Cấu hình để dán proxy.', open: 'proxy' } };
  }
  const scanSheet = sheetName || source.scanSheet || source.displaySheet;
  if (!scanSheet) return { request: null, problem: { message: 'Vui lòng chọn sheet để quét.' } };
  const modeLabel = SCRAPE_MODE_LABELS[scrapeMode] || SCRAPE_MODE_LABELS.request;
  const extras = (state.useProxy ? ' • proxy bật' : '') + (useTikTokSession ? ' • cookie TikTok' : '');
  return {
    request: {
      // Never include proxy text or cookies in the log line.
      logLine: `Bắt đầu quét ${config.label} • sheet "${scanSheet}" • file: ${source.fileId || 'chưa rõ'} • ${modeLabel}${extras} • luồng: ${workers} • đối tác: ${partners.length ? partners.join(', ') : 'tất cả'}.`,
      payload: {
        action: 'start',
        file_id: source.fileId,
        platform,
        workers,
        scrape_mode: scrapeMode,
        use_proxy: state.useProxy,
        proxy_text: proxyText,
        ...(useThreadsSession ? { use_threads_session: true, threads_session_generation: threads.generation } : {}),
        ...(useTikTokSession ? { use_tiktok_session: true } : {}),
        sheet_name: scanSheet,
        partners,
        partner: partners.length === 1 ? partners[0] : '',
      },
    },
  };
}

export function startScraping(partners: string[] = [], sheetName = '') {
  const state = get();
  if (!canAdmin(state) || uiIsBusy(state)) return;
  if (state.sourcesInitialized && !activeSource(state).fileId) { notify('Chưa chọn nguồn cho nền tảng này.', 'warn'); return; }
  if (!ws || ws.readyState !== WebSocket.OPEN) { notify('Lỗi: Chưa kết nối được server.', 'error'); return; }
  const { request, problem } = buildScanRequest(state, partners, sheetName);
  if (!request) {
    if (problem) {
      notify(problem.message, 'warn');
      if (problem.open) openModal(problem.open);
    }
    return;
  }
  if (request.payload.sheet_name !== activeSource(state).scanSheet) updateSource(state.activePlatform, { scanSheet: String(request.payload.sheet_name) });
  addLog(request.logLine);
  ws.send(JSON.stringify(request.payload));
  set(s => ({ workspaceTab: 'live', sourceDrawerOpen: false, settingsDrawerOpen: false, scan: { ...clearLive(s.scan), phase: 'starting', terminalKey: '' } }));
}

/** Socket test seam. */
export function setSocketForTests(socket: WebSocket | null) {
  ws = socket;
}

/** Send a command on the open socket; false (with a toast) when it is not connected. */
export function sendScanCommand(payload: Record<string, unknown>): boolean {
  if (!ws || ws.readyState !== WebSocket.OPEN) { notify('Lỗi: Chưa kết nối được server.', 'error'); return false; }
  ws.send(JSON.stringify(payload));
  return true;
}

/** Ends the retry cooldown now: the run completes with the main-pass results, no retry pass. */
export function skipRetryWait() {
  const scan = get().scan;
  if (!canAdmin() || !scan.retryWait || scan.retrySkipRequested) return;
  if (!sendScanCommand({ action: 'skip_retry', platform: get().activePlatform })) return;
  set(s => ({ scan: { ...s.scan, retrySkipRequested: true } }));
  addLog('Đã gửi lệnh bỏ qua quét lại.');
}

export function cancelScraping() {
  if (!canAdmin()) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) { notify('Lỗi: Chưa kết nối được server.', 'error'); return; }
  ws.send(JSON.stringify({ action: 'cancel', platform: get().activePlatform }));
  set(s => ({ scan: { ...s.scan, phase: 'cancelling' } }));
  addLog('Đã gửi lệnh hủy quét.');
}

// ---- Failed and duplicate lists -------------------------------------------------------

export async function copyFailedLinks(kind: 'nostats' | 'errors') {
  const items = failedLinksForKind(get().scan.failed, kind);
  const label = kind === 'nostats' ? 'link không số liệu' : 'link lỗi';
  if (!items.length) { notify(`Không có ${label} để copy.`, 'warn'); return; }
  try {
    await copyText(items.map(item => item.url).join('\n'));
    notify(`Đã copy ${items.length} ${label}.`, 'success');
  } catch (error) {
    notify(`Lỗi copy link: ${errorMessage(error, '')}`, 'error');
  }
}

export function clearFailedLinks(kind: 'nostats' | 'errors') {
  set(s => ({ scan: { ...s.scan, failed: s.scan.failed.filter(item => (kind === 'nostats' ? !isNoStatsStatus(item.status) : isNoStatsStatus(item.status))) } }));
  addLog(`Đã xóa danh sách link ${kind === 'nostats' ? 'không số liệu' : 'lỗi quét'}.`);
}

export async function copyDuplicateLinks() {
  const items = get().scan.duplicates;
  if (!items.length) { notify('Không có link trùng để copy.', 'warn'); return; }
  const text = items.map(item => {
    const locations = Array.from(groupDuplicateLocations(item.locations).entries()).map(([sheet, rows]) => `${sheet}: dòng ${rows.join(', ')}`).join(' • ');
    return `${item.url || ''}\n${locations}`;
  }).join('\n\n');
  try {
    await copyText(text);
    notify(`Đã copy ${items.length} link trùng kèm số dòng.`, 'success');
  } catch (error) {
    notify(`Lỗi copy link: ${errorMessage(error, '')}`, 'error');
  }
}

export function clearDuplicateLinks() {
  set(s => ({ scan: { ...s.scan, duplicates: [], duplicateRowCount: 0 } }));
  addLog('Đã xóa danh sách link trùng.');
}

// ---- Proxy ----------------------------------------------------------------------------

export async function refreshProxyStatus() {
  if (!canAdmin()) return;
  try {
    const { data } = await getJson<{ text?: string; count?: number }>('/proxy-list', { quietForbidden: true });
    set({ proxyText: String(data.text || ''), proxyCount: Number(data.count || 0) });
  } catch {
    set({ proxyCount: 0 });
  }
}

export function setProxyText(text: string) {
  set({ proxyText: text });
}

// ---- Cookie sessions ------------------------------------------------------------------

const cookieRevision: Record<CookieKind, number> = { threads: 0, tiktok: 0 };
/** Whether this page load already had a cookie status (the first one keeps the saved choice). */
const cookieStatusSeen: Record<CookieKind, boolean> = { threads: false, tiktok: false };
const COOKIE_PATHS: Record<CookieKind, string> = { threads: '/threads-session', tiktok: '/tiktok-session' };
/** Matches MAX_IMPORT_BYTES in riviu/platforms/threads_session.py. */
export const COOKIE_IMPORT_MAX_BYTES = 256 * 1024;

function applyCookieStatus(kind: CookieKind, status: CookieStatus | undefined) {
  const first = !cookieStatusSeen[kind];
  cookieStatusSeen[kind] = true;
  if (get().storage === 'postgres') {
    // The toggle is a saved per-platform setting: a cookie imported or replaced in this page
    // starts switched off (like app.js); the first status of a page load keeps the saved choice.
    const previous = get().cookies[kind];
    const next = status || { configured: false, state: 'unknown', generation: '' };
    set(state => ({ cookies: { ...state.cookies, [kind]: next } }));
    const platform: PlatformKey = kind;
    if (!first && next.configured && previous.generation !== next.generation && get().scanSettings[platform].useSession) {
      void updateScanSettings(platform, { useSession: false });
    }
    return;
  }
  set(state => {
    const previous = state.cookies[kind];
    const next = status || { configured: false, state: 'unknown', generation: '' };
    const useKey = kind === 'threads' ? 'useThreadsSession' : 'useTikTokSession';
    // A new or removed session never stays switched on by itself.
    const resetUse = previous.generation !== next.generation || !next.configured;
    return { cookies: { ...state.cookies, [kind]: next }, ...(resetUse ? { [useKey]: false } : {}) };
  });
}

export async function refreshCookieStatus(kind: CookieKind) {
  if (!canAdmin()) return;
  const revision = ++cookieRevision[kind];
  try {
    const { data } = await getJson<CookieStatus>(`${COOKIE_PATHS[kind]}/status`, { cache: 'no-store', quietForbidden: true });
    if (revision === cookieRevision[kind] && !get().cookieBusy[kind]) applyCookieStatus(kind, data);
  } catch {
    if (revision === cookieRevision[kind] && !get().cookieBusy[kind]) applyCookieStatus(kind, { configured: false, state: 'unknown', message: 'Không tải được trạng thái cookie.' });
  }
}

/** Import (body = cookie JSON), verify or delete a cookie session. Returns the feedback line. */
export async function cookieAction(kind: CookieKind, action: 'import' | 'verify' | 'delete', body = ''): Promise<string> {
  const label = kind === 'threads' ? 'Threads' : 'TikTok';
  if (!canAdmin() || uiIsBusy()) return '';
  if (action === 'import' && (!body.trim() || new TextEncoder().encode(body).length > COOKIE_IMPORT_MAX_BYTES)) {
    return 'Nhập JSON cookie hợp lệ, tối đa 256 KiB.';
  }
  set(state => ({ cookieBusy: { ...state.cookieBusy, [kind]: true } }));
  ++cookieRevision[kind];
  try {
    const response = await apiFetch(action === 'delete' ? COOKIE_PATHS[kind] : `${COOKIE_PATHS[kind]}/${action}`, {
      method: action === 'delete' ? 'DELETE' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: action === 'import' ? body : undefined,
      cache: 'no-store',
    });
    const data = (await response.json().catch(() => ({}))) as { error?: string; status?: CookieStatus; check?: { state?: string; message?: string } } & CookieStatus;
    if (!response.ok) throw new Error(data.error || `Không xử lý được phiên ${label}.`);
    applyCookieStatus(kind, data.status || data);
    let detail = action === 'delete' ? 'Đã xóa phiên khỏi kho bảo mật.' : action === 'verify' ? 'Đã kiểm tra lại phiên.' : 'Đã import và lưu phiên an toàn.';
    if (data.check && data.check.state !== 'valid') {
      detail = `${data.check.message || 'Cookie chưa xác minh hợp lệ.'}${action === 'import' ? ' Phiên đã lưu trước đó được giữ nguyên.' : ''}`;
    }
    return detail;
  } catch (error) {
    return errorMessage(error, `Không xử lý được phiên ${label}.`);
  } finally {
    set(state => ({ cookieBusy: { ...state.cookieBusy, [kind]: false } }));
  }
}

export function setUseSession(kind: CookieKind, value: boolean) {
  set(kind === 'threads' ? { useThreadsSession: value } : { useTikTokSession: value });
}

// ---- Scan settings (server side, per platform) -----------------------------------------

const WORKER_CHOICES = new Set<number>([1, 3, 5, 8, 10, 20, 25, 30, 50]);

export function normalizedScanSettings(platform: PlatformKey, raw: unknown): ScanSettings {
  const base = defaultScanSettings(platform);
  if (!raw || typeof raw !== 'object') return base;
  const record = raw as Record<string, unknown>;
  const workers = Number(record.workers);
  const mode = record.scrapeMode;
  return {
    workers: WORKER_CHOICES.has(workers) ? workers : base.workers,
    scrapeMode: mode === 'request' || mode === 'hybrid' || mode === 'browser' ? mode : base.scrapeMode,
    useProxy: typeof record.useProxy === 'boolean' ? record.useProxy : base.useProxy,
    useSession: typeof record.useSession === 'boolean' ? record.useSession : base.useSession,
  };
}

let scanSettingsSave: Promise<void> = Promise.resolve();

export async function loadScanSettings() {
  if (!canAdmin()) return;
  try {
    const { response, data } = await getJson<{ platforms?: Record<string, unknown>; error?: string }>('/scan-settings', { cache: 'no-store', quietForbidden: true });
    if (!response.ok || !data.platforms) throw new Error(data.error || 'Không tải được cấu hình quét.');
    const platforms = data.platforms;
    set({ scanSettings: Object.fromEntries(PLATFORM_KEYS.map(key => [key, normalizedScanSettings(key, platforms[key])])) as Record<PlatformKey, ScanSettings> });
  } catch (error) {
    addLog(`Không tải được cấu hình quét đã lưu: ${errorMessage(error, '')}`);
  }
}

/** Change one platform's toolbar and save every platform's settings (saves run in order). */
export function updateScanSettings(platform: PlatformKey, patch: Partial<ScanSettings>): Promise<void> {
  set(state => ({ scanSettings: { ...state.scanSettings, [platform]: { ...state.scanSettings[platform], ...patch } } }));
  if (!canAdmin()) return scanSettingsSave;
  const body = { platforms: get().scanSettings };
  scanSettingsSave = scanSettingsSave.then(async () => {
    try {
      const { response, data } = await postJson<{ error?: string }>('/scan-settings', body, { quietForbidden: true });
      if (!response.ok) throw new Error(data.error || 'Không lưu được cấu hình quét.');
    } catch (error) {
      addLog(`Không lưu được cấu hình quét: ${errorMessage(error, '')}`);
    }
  });
  return scanSettingsSave;
}

// ---- Google ---------------------------------------------------------------------------

export async function refreshGoogleStatus() {
  if (!canAdmin()) return;
  try {
    const { data } = await getJson<GoogleStatus>('/google-oauth-status', { quietForbidden: true });
    // A configured service account wins over the OAuth login (see /google-oauth-status).
    set({ googleStatus: data, googleAuthorized: Boolean(data.ready ?? data.valid ?? data.authorized) });
  } catch (error) {
    console.error(error);
  }
}

async function afterGoogleChange() {
  await refreshGoogleStatus();
  await updateFileList();
}

export async function uploadGoogleServiceAccount(file: File) {
  if (!canAdmin()) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const { response, data } = await postForm<{ success?: boolean; error?: string; email?: string }>('/google-service-account', form);
    if (!response.ok || !data.success) throw new Error(data.error || 'Không nạp được tài khoản dịch vụ');
    notify(`Đã nạp tài khoản dịch vụ ${data.email}. Chia sẻ Google Sheet cho email này với quyền Người chỉnh sửa.`, 'success');
    await afterGoogleChange();
  } catch (error) {
    notify(`Lỗi tài khoản dịch vụ: ${errorMessage(error, '')}`, 'error');
  }
}

export async function removeGoogleServiceAccount() {
  if (!canAdmin()) return;
  try {
    const { response, data } = await deleteJson<{ success?: boolean; error?: string }>('/google-service-account');
    if (!response.ok || !data.success) throw new Error(data.error || 'Không gỡ được tài khoản dịch vụ');
    notify('Đã gỡ tài khoản dịch vụ.', 'success');
  } catch (error) {
    notify(`Lỗi tài khoản dịch vụ: ${errorMessage(error, '')}`, 'error');
  } finally {
    await afterGoogleChange();
  }
}

export async function uploadGoogleOauthClient(file: File) {
  if (!canAdmin()) return;
  const form = new FormData();
  form.append('file', file);
  try {
    const { response, data } = await postForm<{ success?: boolean; error?: string }>('/google-oauth-client', form);
    if (!response.ok || !data.success) throw new Error(data.error || 'Không nạp được OAuth client');
    notify('Đã cài cấu hình Google cho ứng dụng. Từ giờ chỉ cần bấm "Đăng nhập Google".', 'success');
    await refreshGoogleStatus();
  } catch (error) {
    notify(`Lỗi OAuth client: ${errorMessage(error, '')}`, 'error');
  }
}

export async function connectGoogleOAuth() {
  if (!canAdmin()) return;
  try {
    const { response, data } = await postJson<{ success?: boolean; error?: string; pasteBack?: boolean; authUrl?: string }>('/google-oauth-login', {});
    if (response.ok && data.pasteBack && data.authUrl) {
      // Server mode: the user approves in their own browser and pastes the localhost address back.
      set({ googlePasteUrl: data.authUrl });
      openModal('googlePaste');
      return;
    }
    if (!response.ok || !data.success) throw new Error(data.error || 'Đăng nhập Google thất bại');
    notify('Đăng nhập Google thành công.', 'success');
  } catch (error) {
    notify(`Lỗi đăng nhập Google: ${errorMessage(error, '')}`, 'error');
  } finally {
    await afterGoogleChange();
  }
}

export async function submitGooglePaste(pasted: string): Promise<string> {
  const value = pasted.trim();
  if (!value) return 'Hãy dán địa chỉ localhost mà Google chuyển tới.';
  try {
    const { response, data } = await postJson<{ success?: boolean; error?: string }>('/google-oauth-complete', { url: value });
    if (!response.ok || !data.success) throw new Error(data.error || 'Đăng nhập Google thất bại');
    closeModal();
    notify('Đăng nhập Google thành công.', 'success');
    await afterGoogleChange();
    return '';
  } catch (error) {
    return errorMessage(error, 'Đăng nhập Google thất bại');
  }
}

// ---- Startup --------------------------------------------------------------------------

export async function startApp(viewer: Viewer) {
  set({ viewer });
  await loadSourcePreferences();
  await updateFileList({ applyGoogleSheetUrl: true });
  // Inactive platforms: load each saved workbook's label and sheets for its source row.
  for (const platform of PLATFORM_KEYS) {
    const source = get().sources[platform];
    if (platform === get().activePlatform || !source.fileId) continue;
    try {
      const { response, data } = await fetchFileList(platform, source.fileId);
      if (!response.ok) { updateSource(platform, { fileId: '' }); continue; }
      updateSource(platform, { sheets: data.sheets || [], hiddenSheets: data.hiddenSheets || [], label: data.currentLabel || data.current || '' });
    } catch { /* leave the row as saved */ }
  }
  await loadPreview();
  if (isAdmin(viewer)) {
    await refreshProxyStatus();
    await refreshCookieStatus('threads');
    await refreshCookieStatus('tiktok');
  }
  connectWS();
}

export const __test = { beginReadRequest, readSequence, bumpRevision: () => ++sourceRevision, prefs };
