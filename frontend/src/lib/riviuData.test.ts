import { describe, expect, it } from 'vitest';
import { columnTotals, filterRows, lastUpdateText, metricColumns, partnerSummary, scanStatusView, shortLink, type RdRow } from './riviuData';
import { addSlot, formatScheduleTime, normalizeOverview, runCountsText } from './schedule';
import { isAdminPage, parseVersion } from './version';

function row(patch: Partial<RdRow>): RdRow {
  return {
    id: 1, ngayAir: '12/10/2026', channel: 'kenh', link: 'https://www.tiktok.com/@kenh/video/7400000000000015838', partners: ['A'],
    views: null, likes: null, comments: null, saves: null, shares: null, updatedAt: '', status: '', ...patch,
  };
}

describe('totals keep unknown apart from zero', () => {
  it('stays blank only when nothing in the column is known', () => {
    const totals = columnTotals([row({ views: null }), row({ views: null })], 'tiktok');
    expect(totals.views).toBeNull();
  });

  it('sums known values, and a column of confirmed zeros is 0', () => {
    const totals = columnTotals([row({ views: 10, likes: 0 }), row({ views: null, likes: 0 }), row({ views: 5, likes: null })], 'tiktok');
    expect(totals.views).toBe(15);
    expect(totals.likes).toBe(0);
  });

  it('uses REPOST on Threads where TikTok has LƯỢT LƯU', () => {
    expect(metricColumns('threads').map(column => column.key)).toEqual(['views', 'likes', 'comments', 'reposts', 'shares']);
    expect(metricColumns('tiktok').map(column => column.key)).toEqual(['views', 'likes', 'comments', 'saves', 'shares']);
    expect(columnTotals([row({ reposts: 3 }), row({ reposts: 4 })], 'threads').reposts).toBe(7);
  });
});

describe('partner summary (Tổng kết)', () => {
  it('counts a link for each of its partners, merges partner case, keeps unknown apart from zero', () => {
    const summary = partnerSummary([
      row({ id: 1, partners: ['Cafe Mây'], views: 100, likes: 0, updatedAt: '28/10/2026-12:30' }),
      row({ id: 2, partners: ['cafe mây', 'Spa'], views: 50, likes: null, updatedAt: '29/10/2026-08:15' }),
      row({ id: 3, partners: ['Spa'], views: null, likes: null }),
      row({ id: 4, partners: [] , views: 999 }),
    ], 'tiktok');
    expect(summary.map(item => [item.partner, item.links, item.totals.views, item.totals.likes, item.totals.comments, item.updatedAt])).toEqual([
      ['Cafe Mây', 2, 150, 0, null, '29/10/2026-08:15'],
      ['Spa', 2, 50, null, null, '29/10/2026-08:15'],
    ]);
  });

  it('sums REPOST on Threads', () => {
    expect(partnerSummary([row({ partners: ['A'], reposts: 2 }), row({ partners: ['A'], reposts: 5 })], 'threads')[0].totals.reposts).toBe(7);
  });
});

describe('table helpers', () => {
  it('filters by partner and by accent-insensitive search', () => {
    const rows = [row({ id: 1, channel: 'Bếp Nhà', partners: ['Cafe Mây'] }), row({ id: 2, channel: 'other', partners: ['Spa'] })];
    expect(filterRows(rows, { search: 'bep nha', partner: '' }).map(item => item.id)).toEqual([1]);
    expect(filterRows(rows, { search: '', partner: 'Spa' }).map(item => item.id)).toEqual([2]);
    expect(filterRows(rows, { search: 'cafe may', partner: 'Spa' })).toEqual([]);
  });

  it('labels the scan status', () => {
    expect(scanStatusView(row({ status: '' })).label).toBe('Chưa quét');
    expect(scanStatusView(row({ status: 'Success' }))).toMatchObject({ label: 'OK', tone: 'success' });
    expect(scanStatusView(row({ status: 'Error: Không đọc được số liệu' }))).toMatchObject({ label: 'Lỗi', tone: 'error', title: 'Không đọc được số liệu' });
    expect(scanStatusView(row({ status: 'Ẩn số liệu' })).tone).toBe('warning');
  });

  it('shortens links to the post id, or host and path for short links', () => {
    expect(shortLink('https://www.tiktok.com/@kenh6/video/7400000000000015838')).toEqual({ handle: '@kenh6', label: '…0015838' });
    expect(shortLink('https://www.tiktok.com/@kenh6/photo/7400000000000015839?is_from_webapp=1').label).toBe('…0015839');
    expect(shortLink('https://www.threads.com/@kenh22/post/D000000000x')).toEqual({ handle: '@kenh22', label: 'D000000000x' });
    expect(shortLink('https://vt.tiktok.com/ZSxQ4KHvk/').label).toBe('vt.tiktok.com/ZSxQ4KHvk');
  });

  it('reports the newest update of the month', () => {
    expect(lastUpdateText([row({ updatedAt: '27/10/2026-09:00' }), row({ updatedAt: '28/10/2026-12:30' }), row({})])).toBe('Số liệu cập nhật lúc 12:30, 28/10/2026');
    expect(lastUpdateText([row({})])).toBe('Chưa có lần quét nào cho tháng này');
  });
});

describe('schedule helpers', () => {
  it('adds slots sorted and refuses duplicates or bad times', () => {
    const slots = addSlot([{ time: '21:00', enabled: true, months: ['2026-09'] }], '08:30');
    expect(slots).toEqual([{ time: '08:30', enabled: true, months: ['latest'] }, { time: '21:00', enabled: true, months: ['2026-09'] }]);
    expect(addSlot([{ time: '21:00', enabled: true, months: ['latest'] }], '21:00')).toBe('Đã có khung giờ 21:00.');
    expect(addSlot([], '25:00')).toMatch(/HH:MM/);
  });

  it('reads the server overview and formats times in the schedule clock', () => {
    const overview = normalizeOverview({ schedule: { platforms: { tiktok: { slots: [
      { time: '07:00', enabled: false }, { time: 'x' }, { time: '13:15', months: ['2025-06', 'bad', 'latest', '2026-09'] },
    ], options: { workers: 5 } } } }, runs: [] });
    // Months: the newest month when missing; "latest" first, then newest first, invalid values dropped.
    expect(overview.schedule.platforms.tiktok.slots).toEqual([
      { time: '07:00', enabled: false, months: ['latest'] }, { time: '13:15', enabled: true, months: ['latest', '2026-09', '2025-06'] },
    ]);
    expect(overview.schedule.platforms.tiktok.options).toMatchObject({ workers: 5, retryFailed: true });
    expect(overview.schedule.platforms.threads.options.scrapeMode).toBe('hybrid');
    expect(formatScheduleTime('2026-10-28T21:00:00+07:00')).toBe('21:00 · 28/10');
    expect(runCountsText({ counts: { total: 10, success: 8, error: 2 } })).toBe('8/10 thành công · 2 lỗi');
  });
});

describe('page selection', () => {
  it('chooses storage and page', () => {
    expect(parseVersion({ storage: 'postgres' }).storage).toBe('postgres');
    expect(parseVersion({}).storage).toBe('workbook');
    expect(isAdminPage('/admin', { serverMode: true, role: 'admin' })).toBe(true);
    expect(isAdminPage('/admin', { serverMode: true, role: 'viewer' })).toBe(false);
    expect(isAdminPage('/', { serverMode: true, role: 'admin' })).toBe(false);
    // Source mode has no /admin: the local admin works at /.
    expect(isAdminPage('/', { serverMode: false, role: 'admin' })).toBe(true);
  });
});
