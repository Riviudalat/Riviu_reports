// Scan run state driven by the /ws protocol, ported from app.js as pure functions so the
// stale-run rules can be tested without a socket.
import { formatDuration } from './format';
import { platformConfig, isPlatform, type PlatformKey } from './platforms';

export type ScanPhase = 'idle' | 'starting' | 'running' | 'saving' | 'cancelling' | 'completed' | 'failed' | 'cancelled';
export type ResultKind = 'success' | 'nostats' | 'error';

export interface RunContext {
  runId?: string;
  platform?: string;
  fileId?: string;
  sheetName?: string;
}

export interface ProgressData extends RunContext {
  total?: number;
  processed?: number;
  success?: number;
  hidden?: number;
  error?: number;
  done?: boolean;
  cancelled?: boolean;
  phase?: string;
  mode?: string;
  partner?: string;
  workers?: number;
  usesBrowser?: boolean;
  rate?: number | string;
  etaSeconds?: number | null;
  stopReason?: string;
  message?: string;
  /** Retry pass over transient failures: during its cooldown retryWaitSeconds/retryAt are set. */
  retryPass?: boolean;
  retryWaitSeconds?: number;
  /** Epoch seconds when the retry pass starts. */
  retryAt?: number;
  retryCount?: number;
  /** A finished run's note (e.g. Threads posts that need Cookie Threads). */
  notice?: string;
  loginRequired?: number;
}

export interface ResultRow extends RunContext {
  id?: string | number;
  url?: string;
  channelName?: string;
  views?: unknown;
  likes?: unknown;
  comments?: unknown;
  saves?: unknown;
  shares?: unknown;
  status?: string;
  missingMetrics?: string[];
  videoLink?: boolean;
  singlePartner?: boolean;
  worker?: string | number;
}

let liveRowSeq = 0;

export interface LiveRow extends ResultRow {
  /** id (or url) of the link: the same link counted once, latest result wins. */
  key: string;
  /** Unique per received row, for rendering. */
  seq: number;
  kind: ResultKind;
  rowPlatform: PlatformKey;
}

export interface FailedItem {
  id?: string | number;
  sheetName?: string;
  url?: string;
  status?: string;
  worker?: string | number;
}

export interface DuplicateItem {
  id?: string | number;
  url?: string;
  locations?: Array<{ sheetName?: string; row?: number | string }>;
}

export interface SessionSnapshot extends RunContext {
  running?: boolean;
  status?: ProgressData;
  results?: ResultRow[];
}

export interface SocketMessage extends RunContext {
  type: 'session' | 'log' | 'status' | 'data' | 'duplicates' | string;
  data?: unknown;
  row?: ResultRow;
  message?: string;
  level?: string;
  details?: LogDetails | null;
}

export interface LogDetails {
  kind?: 'scrape_ok' | 'scrape_hidden' | 'scrape_error' | string;
  processed?: number;
  total?: number;
  worker?: string | number;
  elapsed?: number | string;
  rows?: string;
  url?: string;
  channel?: string;
  status?: string;
  attempts?: number;
  metrics?: { views?: unknown; likes?: unknown; comments?: unknown; saves?: unknown; shares?: unknown };
}

export interface ScanState {
  phase: ScanPhase;
  runContext: RunContext | null;
  /** `${runId}:${phase}` of the last terminal status handled, so it runs once per run. */
  terminalKey: string;
  progress: ProgressData | null;
  /** Newest first. */
  rows: LiveRow[];
  /** Oldest first, like app.js; views reverse it. */
  failed: FailedItem[];
  duplicates: DuplicateItem[];
  duplicateRowCount: number;
  /** The retry-pass cooldown of the tracked run, or null. */
  retryWait: RetryWait | null;
  /** "Bỏ qua quét lại" was sent for the current cooldown. */
  retrySkipRequested: boolean;
}

export interface RetryWait {
  runId: string;
  /** Epoch milliseconds when the retry pass starts. */
  at: number;
  count: number;
  success: number;
  total: number;
}

export const TERMINAL_SCAN_PHASES: readonly ScanPhase[] = ['completed', 'failed', 'cancelled'];
export const BUSY_SCAN_PHASES: readonly ScanPhase[] = ['starting', 'running', 'saving', 'cancelling'];

export function initialScanState(): ScanState {
  return { phase: 'idle', runContext: null, terminalKey: '', progress: null, rows: [], failed: [], duplicates: [], duplicateRowCount: 0, retryWait: null, retrySkipRequested: false };
}

export function scanIsBusy(phase: ScanPhase): boolean {
  return BUSY_SCAN_PHASES.includes(phase);
}

// ---- Classification -------------------------------------------------------------------

export function isNoStatsStatus(status: unknown): boolean {
  const text = String(status || '');
  return text.includes('Ẩn số liệu') || text.startsWith('Partial:') || text.includes('không trả số liệu') || text.includes('không trả lượt xem');
}

export function classifyLiveResult(row: ResultRow, platform: PlatformKey): ResultKind {
  const status = String(row.status || '');
  const confirmed = [row.views, row.likes, row.comments, row.saves, row.shares].some(value => value !== null && value !== undefined && value !== '');
  if (status === 'Success' || (platformConfig(platform).partialWithMetricsIsSuccess && status.startsWith('Partial:') && confirmed)) return 'success';
  return isNoStatsStatus(status) ? 'nostats' : 'error';
}

export function liveStatusLabel(row: LiveRow): string {
  if (row.kind === 'success') return 'OK';
  if (row.kind === 'nostats') return String(row.status || '').startsWith('Partial:') ? 'THIẾU SỐ' : 'KHÔNG SỐ LIỆU';
  return 'LỖI';
}

export function failedLinksForKind(failed: readonly FailedItem[], kind: 'all' | 'nostats' | 'errors' = 'all'): FailedItem[] {
  const newestFirst = failed.slice().reverse();
  if (kind === 'nostats') return newestFirst.filter(item => isNoStatsStatus(item.status));
  if (kind === 'errors') return newestFirst.filter(item => !isNoStatsStatus(item.status));
  return newestFirst;
}

// ---- Progress -------------------------------------------------------------------------

export function progressPlatform(data: ProgressData, state: ScanState, activePlatform: PlatformKey): PlatformKey {
  const candidate = data.platform || state.runContext?.platform || activePlatform;
  return isPlatform(candidate) ? candidate : activePlatform;
}

export interface ResultCounts {
  success: number;
  hidden: number;
  error: number;
}

/**
 * An older runner may count Partial as hidden while this UI renders those rows OK; reconcile
 * from the rows only when the complete processed batch is known.
 */
export function progressResultCounts(data: ProgressData, rows: readonly LiveRow[], platform: PlatformKey): ResultCounts {
  const counts = { success: Number(data.success || 0), hidden: Number(data.hidden || 0), error: Number(data.error || 0) };
  // rows are newest first; insert oldest first so the latest result of a link wins.
  const keys = new Map<string, ResultKind>();
  for (let index = rows.length - 1; index >= 0; index -= 1) keys.set(rows[index].key, rows[index].kind);
  if (platformConfig(platform).reconcileCountsFromRows && Number(data.processed || 0) > 0 && keys.size === Number(data.processed)) {
    counts.success = counts.hidden = counts.error = 0;
    for (const kind of keys.values()) counts[kind === 'nostats' ? 'hidden' : kind] += 1;
  }
  return counts;
}

export function progressPercent(data: ProgressData | null): number {
  if (!data || !data.total) return 0;
  return (Number(data.processed || 0) / Number(data.total)) * 100;
}

export function progressSummaryText(data: ProgressData | null): string {
  if (!data) return 'Sẵn sàng chờ lệnh...';
  const pct = progressPercent(data);
  const parts: string[] = [];
  if (data.phase === 'starting') parts.push(`Đang khởi tạo: 0/${data.total ?? 0} (${Math.round(pct)}%)`);
  else parts.push(`Tiến độ: ${data.processed ?? 0}/${data.total ?? 0} (${Math.round(pct)}%)`);
  if (data.mode === 'partner' && data.partner) parts.push(`đối tác ${data.partner}`);
  if (data.workers) parts.push(`${data.workers} luồng`);
  if (data.phase === 'starting') parts.push(data.usesBrowser ? 'đang mở trình duyệt' : 'đang khởi tạo luồng Request');
  else if (data.rate) parts.push(`${data.rate} link/phút`);
  if (data.etaSeconds !== null && data.etaSeconds !== undefined && !data.done) parts.push(`còn ~${formatDuration(data.etaSeconds)}`);
  return parts.join(' • ');
}

export type StatusTone = '' | 'success' | 'warn' | 'cancelled';

export function progressStatusView(phase: ScanPhase, counts: ResultCounts, platform: PlatformKey, stopReason = ''): [string, StatusTone] {
  if (phase === 'failed') return ['Quét hoặc lưu thất bại', 'warn'];
  if (phase === 'cancelled') return ['Đã huỷ', 'cancelled'];
  if (phase === 'completed' && stopReason === 'rate_limit') return ['Đã dừng: Threads giới hạn tần suất (HTTP 429)', 'warn'];
  if (phase === 'completed') {
    if (counts.hidden > 0 || counts.error > 0) return [platformConfig(platform).completedWithIssues(counts.error), 'warn'];
    return ['Thành công', 'success'];
  }
  if (phase === 'saving') return ['Đang lưu kết quả...', ''];
  return ['', ''];
}

export function phaseFromStatus(data: ProgressData): ScanPhase {
  return (data.phase as ScanPhase) || (data.cancelled ? 'cancelled' : data.done ? 'failed' : 'running');
}

export function terminalLogLine(phase: ScanPhase, stopReason = ''): string {
  if (phase === 'cancelled') return '--- ĐÃ HỦY QUÉT ---';
  if (phase === 'failed') return '--- QUÉT/LƯU THẤT BẠI ---';
  if (stopReason === 'rate_limit') return '--- ĐÃ DỪNG: THREADS GIỚI HẠN TẦN SUẤT (HTTP 429), ĐÃ LƯU KẾT QUẢ ĐÃ QUÉT ---';
  return '--- QUÉT VÀ LƯU HOÀN TẤT ---';
}

// ---- Event routing --------------------------------------------------------------------

export interface ViewContext {
  activePlatform: PlatformKey;
  currentFileId: string;
}

/**
 * Whether a socket event must be ignored by this page: a terminal ("done") status/event for a
 * workbook other than the one this page shows belongs to a run this page is not following.
 * The run this page tracks (`trackedRunId`) always ends here, or the page would stay
 * "đang quét" after the scan finished.
 */
export function isStaleTerminalEvent(message: SocketMessage, view: ViewContext, trackedRunId = ''): boolean {
  const data = message.data as { done?: boolean } | undefined;
  const trackedRun = Boolean(message.runId) && message.runId === trackedRunId;
  return Boolean(data?.done && message.platform && !trackedRun && (message.platform !== view.activePlatform || message.fileId !== view.currentFileId));
}

/** Events other than logs carry the run context; a new runId resets the live results. */
export function shouldApplyRunContext(message: SocketMessage): boolean {
  return Boolean(message.runId) && message.type !== 'log';
}

export function clearLive(state: ScanState, { duplicates = true } = {}): ScanState {
  return { ...state, progress: null, rows: [], failed: [], ...(duplicates ? { duplicates: [], duplicateRowCount: 0 } : {}) };
}

export function applyRunContext(state: ScanState, context: RunContext): { state: ScanState; changedRun: boolean } {
  if (!context?.runId) return { state, changedRun: false };
  const changedRun = state.runContext?.runId !== context.runId;
  const runContext: RunContext = {
    runId: context.runId ?? state.runContext?.runId,
    platform: context.platform ?? state.runContext?.platform,
    fileId: context.fileId ?? state.runContext?.fileId,
    sheetName: context.sheetName ?? state.runContext?.sheetName,
  };
  let next: ScanState = { ...state, runContext };
  if (changedRun) next = { ...clearLive(next), terminalKey: '', retryWait: null, retrySkipRequested: false };
  return { state: next, changedRun };
}

export function appendResult(state: ScanState, row: ResultRow, context: RunContext, activePlatform: PlatformKey): ScanState {
  const candidate = row.platform || context.platform || state.runContext?.platform || activePlatform;
  const rowPlatform = isPlatform(candidate) ? candidate : activePlatform;
  const kind = classifyLiveResult(row, rowPlatform);
  const key = String(row.id ?? row.url);
  liveRowSeq += 1;
  const live: LiveRow = { ...row, key, seq: liveRowSeq, kind, rowPlatform };
  const rows = [live, ...state.rows];
  const failed = kind === 'success'
    ? state.failed
    : [...state.failed, { id: row.id, sheetName: row.sheetName, url: row.url, status: row.status, worker: row.worker }];
  return { ...state, rows, failed };
}

export interface StatusResult {
  state: ScanState;
  /** Set once per run and terminal phase, and never for a replayed snapshot. */
  finished: { phase: ScanPhase; stopReason: string } | null;
}

// ---- Retry-pass cooldown --------------------------------------------------------------
// A live status is fresh, so the wait counts from now (immune to clock skew); a snapshot replayed
// on (re)connect may be minutes old, so it counts to the server's retryAt (clamped to the wait).

export function retryWaitFrom(data: ProgressData | null | undefined, current: RetryWait | null, runId: string, { snapshot = false, now = Date.now() } = {}): RetryWait | null {
  const seconds = Number(data?.retryWaitSeconds);
  if (!data || data.done || !data.retryPass || !(seconds > 0)) return null;
  if (current && current.runId === runId) return current;
  const serverAt = Number(data.retryAt) * 1000;
  const at = snapshot && serverAt > 0 ? Math.min(Math.max(serverAt, now), now + seconds * 1000) : now + seconds * 1000;
  return { runId, at, count: Number(data.retryCount) || Number(data.error) || 0, success: Number(data.success) || 0, total: Number(data.total) || 0 };
}

export function formatClockTime(ms: number): string {
  const date = new Date(ms);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export function formatCountdown(seconds: number): string {
  const sec = Math.max(Math.ceil(seconds), 0);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

/** The admin's progress line during the cooldown. */
export function retryWaitText(wait: RetryWait, skipRequested: boolean, now = Date.now()): string {
  const main = `Đã quét xong lượt chính (${wait.success}/${wait.total} thành công).`;
  if (skipRequested) return `${main} Đã bỏ qua quét lại, đang kết thúc phiên quét…`;
  const remaining = (wait.at - now) / 1000;
  if (remaining <= 0) return `${main} Sắp quét lại ${wait.count} link lỗi…`;
  return `${main} Chờ quét lại ${wait.count} link lỗi lúc ${formatClockTime(wait.at)} (còn ${formatCountdown(remaining)})`;
}

/** The public viewer's calm line while a scan runs. */
export function viewerScanText(wait: RetryWait | null): string {
  return wait
    ? `Số liệu đã cập nhật. Hệ thống sẽ tự quét lại vài link chưa đọc được lúc ${formatClockTime(wait.at)}.`
    : 'Đang cập nhật số liệu…';
}

/** Progress line outside the cooldown: a retry pass in progress, or a finished run's notice first. */
export function progressLineText(data: ProgressData | null, phase: ScanPhase): string {
  if (!data) return 'Sẵn sàng chờ lệnh...';
  const summary = progressSummaryText(data);
  const terminal = TERMINAL_SCAN_PHASES.includes(phase);
  if (data.retryPass && !terminal) return `Đang quét lại ${Number(data.retryCount) || data.total || 0} link lỗi… • ${summary}`;
  if (data.notice && phase === 'completed') return `${data.notice} • ${summary}`;
  return summary;
}

export function applyStatus(state: ScanState, data: ProgressData, { replay = false, snapshot = false, now = Date.now() } = {}): StatusResult {
  const phase = phaseFromStatus(data);
  const runId = String(state.runContext?.runId || '');
  const retryWait = TERMINAL_SCAN_PHASES.includes(phase) ? null : retryWaitFrom(data, state.retryWait, runId, { snapshot, now });
  const retrySkipRequested = retryWait && retryWait.runId === state.retryWait?.runId ? state.retrySkipRequested : false;
  let next: ScanState = { ...state, progress: data, phase, retryWait, retrySkipRequested };
  let finished: StatusResult['finished'] = null;
  if (TERMINAL_SCAN_PHASES.includes(phase)) {
    const terminalKey = `${next.runContext?.runId || ''}:${phase}`;
    if (next.terminalKey !== terminalKey) {
      next = { ...next, terminalKey };
      if (!replay) finished = { phase, stopReason: data.stopReason || '' };
    }
  }
  return { state: next, finished };
}

export function setDuplicates(state: ScanState, data: unknown): ScanState {
  const payload = (data || {}) as { items?: DuplicateItem[]; duplicateRowCount?: number };
  const duplicates = Array.isArray(payload.items) ? payload.items : [];
  const fallbackCount = duplicates.reduce((total, item) => total + Math.max((Array.isArray(item.locations) ? item.locations.length : 0) - 1, 0), 0);
  const reported = Number(payload.duplicateRowCount);
  const duplicateRowCount = payload.duplicateRowCount !== undefined && payload.duplicateRowCount !== null && Number.isFinite(reported) && reported >= 0 ? reported : fallbackCount;
  return { ...state, duplicates, duplicateRowCount };
}

export function groupDuplicateLocations(locations: DuplicateItem['locations']): Map<string, Array<number | string | undefined>> {
  const grouped = new Map<string, Array<number | string | undefined>>();
  (Array.isArray(locations) ? locations : []).forEach(location => {
    const sheetName = String(location?.sheetName || 'Không rõ sheet');
    if (!grouped.has(sheetName)) grouped.set(sheetName, []);
    grouped.get(sheetName)!.push(location?.row);
  });
  return grouped;
}

/**
 * A snapshot replays the server's last run whenever this page (re)connects. A run that had
 * already finished was saved before this page saw it, so its terminal side effects only run
 * for a run this page was tracking as active.
 */
export function applySessionSnapshot(state: ScanState, session: SessionSnapshot, view: ViewContext): { state: ScanState; follow: boolean } {
  const trackedActiveRun = scanIsBusy(state.phase) && Boolean(session.runId) && state.runContext?.runId === session.runId;
  let next: ScanState = { ...state, phase: session.running ? ((session.status?.phase as ScanPhase) || 'running') : 'idle' };
  let follow = false;
  if (session.running || (session.platform === view.activePlatform && session.fileId === view.currentFileId && view.currentFileId)) {
    follow = Boolean(session.runId);
    next = applyRunContext(next, session).state;
    next = { ...next, rows: [], failed: [], progress: null };
    for (const row of session.results || []) next = appendResult(next, row, session, view.activePlatform);
    if (session.status?.phase || session.status?.done) {
      next = applyStatus(next, session.status, { replay: !session.running && !trackedActiveRun, snapshot: true }).state;
    }
  }
  if (session.running) next = { ...next, phase: (session.status?.phase as ScanPhase) || 'running' };
  return { state: next, follow };
}

// ---- Logs -----------------------------------------------------------------------------

export type LogLevel = 'ERROR' | 'WARN' | 'OK' | 'INFO' | string;

export function detectLogLevel(msg: unknown, explicitLevel = ''): LogLevel {
  const level = String(explicitLevel || '').trim().toUpperCase();
  if (level) return level;
  const text = String(msg || '');
  if (text.includes('LỖI') || text.includes('Lỗi') || text.includes('Mất kết nối') || text.includes('thất bại') || text.includes('Error:')) return 'ERROR';
  if (text.includes('CẢNH BÁO') || text.includes('Đang chờ') || text.includes('Ẩn số liệu')) return 'WARN';
  if (text.includes('OK •') || text.includes('thành công') || text.includes('HOÀN THÀNH') || text.includes('--- QUÉT HOÀN TẤT ---')) return 'OK';
  return 'INFO';
}
