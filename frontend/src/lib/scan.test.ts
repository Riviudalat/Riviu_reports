import { describe, expect, it } from 'vitest';
import {
  appendResult, applyRunContext, applySessionSnapshot, applyStatus, classifyLiveResult, initialScanState, isStaleTerminalEvent, progressResultCounts,
  shouldApplyRunContext, type ScanState,
} from './scan';

const view = { activePlatform: 'tiktok' as const, currentFileId: 'data/a.xlsx' };

function running(runId = 'run-1'): ScanState {
  return applyRunContext({ ...initialScanState(), phase: 'running' }, { runId, platform: 'tiktok', fileId: 'data/a.xlsx', sheetName: 'T9' }).state;
}

describe('stale run events', () => {
  it('drops a terminal event that belongs to another platform or workbook', () => {
    expect(isStaleTerminalEvent({ type: 'status', platform: 'threads', fileId: 'data/a.xlsx', data: { done: true } }, view)).toBe(true);
    expect(isStaleTerminalEvent({ type: 'status', platform: 'tiktok', fileId: 'data/b.xlsx', data: { done: true } }, view)).toBe(true);
  });

  it('keeps terminal events of the shown workbook and every non-terminal event', () => {
    expect(isStaleTerminalEvent({ type: 'status', platform: 'tiktok', fileId: 'data/a.xlsx', data: { done: true } }, view)).toBe(false);
    expect(isStaleTerminalEvent({ type: 'status', platform: 'threads', fileId: 'data/x.xlsx', data: { done: false } }, view)).toBe(false);
  });

  it('applies the run context from every event except logs', () => {
    expect(shouldApplyRunContext({ type: 'data', runId: 'r' })).toBe(true);
    expect(shouldApplyRunContext({ type: 'log', runId: 'r' })).toBe(false);
    expect(shouldApplyRunContext({ type: 'status' })).toBe(false);
  });

  it('resets the live results when a new run starts', () => {
    let state = running('run-1');
    state = appendResult(state, { id: 1, url: 'https://www.tiktok.com/@a/video/1', status: 'Lỗi mạng' }, {}, 'tiktok');
    expect(state.rows).toHaveLength(1);
    expect(state.failed).toHaveLength(1);
    const same = applyRunContext(state, { runId: 'run-1' });
    expect(same.changedRun).toBe(false);
    expect(same.state.rows).toHaveLength(1);
    const next = applyRunContext(state, { runId: 'run-2', platform: 'tiktok' });
    expect(next.changedRun).toBe(true);
    expect(next.state.rows).toHaveLength(0);
    expect(next.state.failed).toHaveLength(0);
    expect(next.state.runContext?.runId).toBe('run-2');
  });
});

describe('terminal status handling', () => {
  it('finishes a run once per run and phase', () => {
    const first = applyStatus(running(), { phase: 'completed', done: true });
    expect(first.finished).toEqual({ phase: 'completed', stopReason: '' });
    const again = applyStatus(first.state, { phase: 'completed', done: true });
    expect(again.finished).toBeNull();
  });

  it('never runs terminal side effects for a replayed snapshot of a finished run', () => {
    const result = applySessionSnapshot(initialScanState(), {
      runId: 'old', platform: 'tiktok', fileId: 'data/a.xlsx', running: false,
      status: { phase: 'completed', done: true, total: 2, processed: 2 }, results: [{ id: 1, status: 'Success', views: 5 }],
    }, view);
    expect(result.state.phase).toBe('completed');
    expect(result.state.rows).toHaveLength(1);
    // A later live status of that same run is not a new finish either.
    expect(applyStatus(result.state, { phase: 'completed', done: true }).finished).toBeNull();
  });

  it('ignores a finished snapshot of another workbook', () => {
    const result = applySessionSnapshot(initialScanState(), {
      runId: 'old', platform: 'threads', fileId: 'data/t.xlsx', running: false, status: { phase: 'completed', done: true }, results: [{ id: 1 }],
    }, view);
    expect(result.state.rows).toHaveLength(0);
    expect(result.follow).toBe(false);
  });

  it('follows a running scan on (re)connect', () => {
    const result = applySessionSnapshot(initialScanState(), {
      runId: 'live', platform: 'threads', fileId: 'data/t.xlsx', running: true, status: { phase: 'running', total: 10, processed: 1 }, results: [],
    }, view);
    expect(result.follow).toBe(true);
    expect(result.state.phase).toBe('running');
    expect(result.state.runContext?.platform).toBe('threads');
  });
});

describe('result classification', () => {
  it('counts a Threads partial with confirmed metrics as success, TikTok partial as no-stats', () => {
    const row = { status: 'Partial: thiếu lượt xem', likes: 3 };
    expect(classifyLiveResult(row, 'threads')).toBe('success');
    expect(classifyLiveResult(row, 'tiktok')).toBe('nostats');
    expect(classifyLiveResult({ status: 'Partial: x' }, 'threads')).toBe('nostats');
    expect(classifyLiveResult({ status: 'Timeout' }, 'tiktok')).toBe('error');
  });

  it('reconciles Threads counts from the rows once the processed batch is complete', () => {
    let state = applyRunContext(initialScanState(), { runId: 'th', platform: 'threads', fileId: 'data/th.xlsx' }).state;
    state = appendResult(state, { id: 1, status: 'Partial: x', views: 4 }, {}, 'threads');
    state = appendResult(state, { id: 2, status: 'Timeout' }, {}, 'threads');
    const counts = progressResultCounts({ processed: 2, success: 0, hidden: 1, error: 1 }, state.rows, 'threads');
    expect(counts).toEqual({ success: 1, hidden: 0, error: 1 });
  });
});
