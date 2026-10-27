import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { emptySource, sourceQuery } from '@/lib/sources';
import { reportPartnersParams, exportReportBody } from '@/features/ReportDialog';
import {
  appStore, buildScanRequest, handleSocketMessage, loadPreview, resetAppState, selectFile, setPlatform, updateFileList, workbookDownloadUrl, type AppState,
} from './app';

const ADMIN = { authenticated: true, username: 'a', role: 'admin' as const, serverMode: true };
const VIEWER = { authenticated: true, username: 'v', role: 'viewer' as const, serverMode: true };

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

let calls: Array<{ url: string; init?: RequestInit }> = [];

function sources(tiktok: Partial<AppState['sources']['tiktok']>, threads: Partial<AppState['sources']['threads']> = {}) {
  return { tiktok: { ...emptySource(), ...tiktok }, threads: { ...emptySource(), ...threads } };
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.startsWith('/list-files')) {
      const params = new URLSearchParams(url.split('?')[1]);
      return jsonResponse({ files: [{ id: 'data/t.xlsx' }, { id: 'data/th.xlsx' }], current: params.get('file_id'), sheets: ['T9'], hiddenSheets: [], currentSheet: 'T9', scanSheet: 'T9' });
    }
    if (url.startsWith('/preview-excel')) {
      const params = new URLSearchParams(url.split('?')[1]);
      return jsonResponse({ file: params.get('file_id'), sheets: ['T9'], currentSheet: 'T9', columns: ['LINK', 'LƯỢT XEM'], data: [] });
    }
    if (url.startsWith('/select-file')) {
      const body = JSON.parse(String(init?.body || '{}'));
      return jsonResponse({ success: true, selected: body.filename, sheet: 'T9', scanSheet: 'T9' });
    }
    return jsonResponse({ success: true });
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function workbookCalls() {
  return calls.filter(call => /^\/(list-files|preview-excel|summary-dashboard|download-excel|report-partners)/.test(call.url));
}

describe('every workbook request names file_id and platform', () => {
  it('sourceQuery keeps an empty file_id instead of dropping it', () => {
    const params = sourceQuery('threads', '');
    expect(params.has('file_id')).toBe(true);
    expect(params.get('platform')).toBe('threads');
  });

  it('report and export requests carry the explicit source', () => {
    const params = reportPartnersParams('threads', 'data/th.xlsx', 'T9', true, 100);
    expect(params.get('file_id')).toBe('data/th.xlsx');
    expect(params.get('platform')).toBe('threads');
    expect(exportReportBody('tiktok', 'data/t.xlsx', ['A'], 'T9', false, 0)).toMatchObject({ file_id: 'data/t.xlsx', platform: 'tiktok' });
  });

  it('file list and preview reads use the active platform and its own file', async () => {
    resetAppState({ viewer: ADMIN, sourcesInitialized: true, sources: sources({ fileId: 'data/t.xlsx' }, { fileId: 'data/th.xlsx' }) });
    await updateFileList();
    await loadPreview();
    expect(workbookCalls().length).toBeGreaterThanOrEqual(2);
    for (const call of workbookCalls()) {
      const params = new URLSearchParams(call.url.split('?')[1]);
      expect(params.get('platform')).toBe('tiktok');
      expect(params.get('file_id')).toBe('data/t.xlsx');
    }
    expect(workbookDownloadUrl()).toContain('file_id=data%2Ft.xlsx');
    expect(workbookDownloadUrl()).toContain('platform=tiktok');
  });

  it('switching platform never reads the other platform\'s workbook', async () => {
    resetAppState({ viewer: ADMIN, sourcesInitialized: true, sources: sources({ fileId: 'data/t.xlsx', sheets: ['T9'] }) });
    await setPlatform('threads');
    await vi.waitFor(() => expect(appStore.get().preview.kind).toBe('empty'));
    expect(appStore.get().activePlatform).toBe('threads');
    expect(calls.some(call => call.url.includes('t.xlsx'))).toBe(false);
    for (const call of workbookCalls()) {
      const params = new URLSearchParams(call.url.split('?')[1]);
      expect(params.get('platform')).toBe('threads');
      expect(params.get('file_id')).toBe('');
    }
  });

  it('the scan payload names the file and platform being scanned', () => {
    resetAppState({ viewer: ADMIN, activePlatform: 'threads', sources: sources({ fileId: 'data/t.xlsx' }, { fileId: 'data/th.xlsx', scanSheet: 'T9' }) });
    const { request } = buildScanRequest(appStore.get(), ['A'], '');
    expect(request?.payload).toMatchObject({ action: 'start', file_id: 'data/th.xlsx', platform: 'threads', sheet_name: 'T9', partners: ['A'], partner: 'A', scrape_mode: 'hybrid' });
    expect(request?.logLine).not.toContain('proxy_text');
  });

  it('never puts the proxy list in the scan log line', () => {
    resetAppState({ viewer: ADMIN, useProxy: true, proxyText: 'user:secret@1.2.3.4:80', sources: sources({ fileId: 'data/t.xlsx', scanSheet: 'T9' }) });
    const { request } = buildScanRequest(appStore.get());
    expect(request?.payload.proxy_text).toBe('user:secret@1.2.3.4:80');
    expect(request?.logLine).not.toContain('secret');
  });
});

describe('viewer source changes stay local', () => {
  it('a viewer choosing a file never POSTs selection or preferences', async () => {
    resetAppState({ viewer: VIEWER, sourcesInitialized: true, sources: sources({}) });
    await selectFile('data/t.xlsx');
    expect(calls.some(call => call.init?.method === 'POST')).toBe(false);
    expect(appStore.get().sources.tiktok.fileId).toBe('data/t.xlsx');
    expect(workbookCalls().every(call => new URLSearchParams(call.url.split('?')[1]).get('file_id') === 'data/t.xlsx')).toBe(true);
  });
});

describe('socket events through the store', () => {
  it('drops a finished status of another workbook and keeps the live view', () => {
    resetAppState({ viewer: ADMIN, sources: sources({ fileId: 'data/t.xlsx' }) });
    handleSocketMessage({ type: 'data', runId: 'r1', platform: 'tiktok', fileId: 'data/t.xlsx', sheetName: 'T9', row: { id: 1, status: 'Success', views: 0 } });
    expect(appStore.get().scan.rows).toHaveLength(1);
    handleSocketMessage({ type: 'status', runId: 'r0', platform: 'tiktok', fileId: 'data/other.xlsx', data: { done: true, phase: 'completed' } });
    expect(appStore.get().scan.rows).toHaveLength(1);
    expect(appStore.get().scan.runContext?.runId).toBe('r1');
    // A new run (different runId) replaces the previous run's rows.
    handleSocketMessage({ type: 'data', runId: 'r2', platform: 'tiktok', fileId: 'data/t.xlsx', sheetName: 'T9', row: { id: 7, status: 'Timeout' } });
    expect(appStore.get().scan.rows.map(row => row.id)).toEqual([7]);
    expect(appStore.get().scan.failed).toHaveLength(1);
  });
});
