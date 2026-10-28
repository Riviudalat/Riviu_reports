import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Viewer } from '@/lib/auth';
import { appStore, handleSocketMessage, resetAppState, setSocketForTests, skipRetryWait, updateScanSettings } from './app';
import {
  buildRdScanRequest, installRiviuDataHooks, loadMonths, loadRows, resetRiviuDataModule, selectRdPlatform, startRdScan,
} from './riviuData';

const ADMIN: Viewer = { authenticated: true, username: 'quantri', role: 'admin', serverMode: true };
const ANON: Viewer = { authenticated: false, username: '', role: 'viewer', serverMode: true };

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

let calls: Array<{ url: string; init?: RequestInit }> = [];
let threadsGeneration = 'gen-2';
const sent: Array<Record<string, unknown>> = [];

const MONTHS = {
  tiktok: { months: [{ thang: '2026-11', label: 'Tháng 11/2026', rows: 0 }, { thang: '2026-10', label: 'Tháng 10/2026', rows: 2 }], defaultThang: '2026-10' },
  threads: { months: [{ thang: '2026-10', label: 'Tháng 10/2026', rows: 1 }], defaultThang: '2026-10' },
};

beforeEach(() => {
  calls = [];
  sent.length = 0;
  threadsGeneration = 'gen-2';
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const params = new URLSearchParams(url.split('?')[1] || '');
    if (url.startsWith('/riviu-data/months')) return json(MONTHS[params.get('platform') as 'tiktok' | 'threads']);
    if (url.startsWith('/riviu-data/rows')) {
      return json({ partners: ['A'], rows: [{ id: 1, link: `https://x/${params.get('platform')}/${params.get('thang')}`, partners: ['A'], views: null, likes: 0, comments: null, shares: null, status: '' }] });
    }
    if (url.startsWith('/threads-session/status')) return json({ configured: true, state: 'valid', generation: threadsGeneration });
    return json({ success: true });
  }));
  resetAppState({ storage: 'postgres', viewer: ADMIN, proxyCount: 2 });
  resetRiviuDataModule();
  installRiviuDataHooks();
  setSocketForTests({ readyState: 1, send: (text: string) => sent.push(JSON.parse(text)) } as unknown as WebSocket);
});

afterEach(() => {
  setSocketForTests(null);
  vi.unstubAllGlobals();
});

const rowsCalls = () => calls.filter(call => call.url.startsWith('/riviu-data/rows'));

describe('months and rows', () => {
  it('opens the server default month (not a future one) and reads its rows', async () => {
    await loadMonths('tiktok');
    expect(appStore.get().rd.thang.tiktok).toBe('2026-10');
    await loadRows();
    const table = appStore.get().rd.table;
    expect(table.state).toBe('ready');
    expect(rowsCalls().at(-1)?.url).toBe('/riviu-data/rows?platform=tiktok&thang=2026-10');
    // Unknown stays null, a confirmed zero stays 0.
    if (table.state === 'ready') expect(table.rows[0]).toMatchObject({ views: null, likes: 0 });
  });

  it('switching platform reads only that platform', async () => {
    await loadMonths('tiktok');
    await selectRdPlatform('threads');
    expect(appStore.get().activePlatform).toBe('threads');
    expect(rowsCalls().at(-1)?.url).toBe('/riviu-data/rows?platform=threads&thang=2026-10');
  });
});

describe('starting a month scan', () => {
  beforeEach(async () => {
    await loadMonths('tiktok');
    await loadMonths('threads');
  });

  it('sends the month, the saved toolbar and no file or proxy text', async () => {
    await updateScanSettings('tiktok', { workers: 5, scrapeMode: 'hybrid', useProxy: true });
    await startRdScan();
    expect(sent).toEqual([{
      action: 'start', platform: 'tiktok', sheet_name: '2026-10', workers: 5, scrape_mode: 'hybrid', use_proxy: true, proxy_text: '', partners: [], retry_failed: true,
    }]);
    expect(appStore.get().scan.phase).toBe('starting');
    // The toolbar was saved server side for every platform.
    const saved = calls.filter(call => call.url === '/scan-settings' && call.init?.method === 'POST').at(-1);
    expect(JSON.parse(String(saved?.init?.body)).platforms.tiktok).toEqual({ workers: 5, scrapeMode: 'hybrid', useProxy: true, useSession: false });
  });

  it('checks the Threads cookie right before starting and sends its current generation', async () => {
    appStore.set({ activePlatform: 'threads', cookies: { ...appStore.get().cookies, threads: { configured: true, state: 'valid', generation: 'gen-1' } } });
    await updateScanSettings('threads', { useSession: true });
    await startRdScan();
    expect(calls.some(call => call.url.startsWith('/threads-session/status'))).toBe(true);
    expect(sent[0]).toMatchObject({ platform: 'threads', use_threads_session: true, threads_session_generation: 'gen-2' });
  });

  it('refuses to start without a Threads cookie and opens its dialog', async () => {
    appStore.set({ activePlatform: 'threads' });
    await updateScanSettings('threads', { useSession: true });
    const { request, problem } = buildRdScanRequest(appStore.get());
    expect(request).toBeNull();
    expect(problem?.open).toBe('threadsCookie');
  });

  it('refuses proxy on with an empty proxy list', async () => {
    appStore.set({ proxyCount: 0 });
    await updateScanSettings('tiktok', { useProxy: true });
    expect(buildRdScanRequest(appStore.get()).problem?.open).toBe('proxy');
  });
});

describe('socket flow on Riviu Data', () => {
  const ctx = { runId: 'run-1', platform: 'tiktok', fileId: 'riviu-data', sheetName: '2026-10' };

  beforeEach(async () => {
    await loadMonths('tiktok');
    await loadRows();
  });

  it('ends the tracked run even when the page moved to another platform, then reloads the numbers', async () => {
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', total: 2, processed: 1 } });
    expect(appStore.get().scan.phase).toBe('running');
    await selectRdPlatform('threads');
    const before = rowsCalls().length;
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'completed', done: true, total: 2, processed: 2 } });
    expect(appStore.get().scan.phase).toBe('completed');
    // The finished run's months are refreshed; the shown platform's table only when it is that run's.
    await vi.waitFor(() => expect(calls.filter(call => call.url === '/riviu-data/months?platform=tiktok').length).toBeGreaterThanOrEqual(2));
    expect(rowsCalls().length).toBe(before);
  });

  it('reloads the shown table when the run finishes', async () => {
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', total: 2, processed: 1 } });
    const before = rowsCalls().length;
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'completed', done: true, total: 2, processed: 2 } });
    await vi.waitFor(() => expect(rowsCalls().length).toBe(before + 1));
  });

  it('enters the retry cooldown (numbers already saved), and "Bỏ qua quét lại" sends skip_retry once', async () => {
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', total: 2, processed: 2 } });
    const before = rowsCalls().length;
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', retryPass: true, retryWaitSeconds: 300, retryAt: Date.now() / 1000 + 300, retryCount: 1, success: 1, total: 2, processed: 2 } });
    expect(appStore.get().scan.retryWait).toMatchObject({ runId: 'run-1', count: 1 });
    await vi.waitFor(() => expect(rowsCalls().length).toBe(before + 1));
    skipRetryWait();
    skipRetryWait();
    expect(sent).toEqual([{ action: 'skip_retry' }]);
    expect(appStore.get().scan.retrySkipRequested).toBe(true);
    handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'completed', done: true, total: 2, processed: 2 } });
    expect(appStore.get().scan.retryWait).toBeNull();
  });

  it('an admin page follows a new run to its platform and month once', async () => {
    await loadMonths('threads');
    handleSocketMessage({ type: 'status', runId: 'run-th', platform: 'threads', fileId: 'riviu-data', sheetName: '2026-10', data: { phase: 'running', total: 1 } });
    expect(appStore.get().activePlatform).toBe('threads');
    // The admin may browse elsewhere while it runs; later events of that run do not pull them back.
    await selectRdPlatform('tiktok');
    handleSocketMessage({ type: 'status', runId: 'run-th', platform: 'threads', fileId: 'riviu-data', sheetName: '2026-10', data: { phase: 'running', total: 1, processed: 1 } });
    expect(appStore.get().activePlatform).toBe('tiktok');
  });

  it('a public viewer keeps its own view and cannot skip the retry', async () => {
    resetAppState({ storage: 'postgres', viewer: ANON });
    resetRiviuDataModule();
    handleSocketMessage({ type: 'status', runId: 'run-th', platform: 'threads', fileId: 'riviu-data', sheetName: '2026-10', data: { phase: 'running', retryPass: true, retryWaitSeconds: 60, total: 1 } });
    expect(appStore.get().activePlatform).toBe('tiktok');
    expect(appStore.get().scan.retryWait).not.toBeNull();
    skipRetryWait();
    expect(sent).toEqual([]);
  });
});
