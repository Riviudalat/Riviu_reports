import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Viewer } from '@/lib/auth';
import type { RdRow } from '@/lib/riviuData';
import { appStore, handleSocketMessage, openModal, resetAppState, setSocketForTests } from '@/state/app';
import { installRiviuDataHooks, resetRiviuDataModule, selectThang } from '@/state/riviuData';
import { RiviuDataLayout } from './RiviuDataApp';

const ADMIN: Viewer = { authenticated: true, username: 'quantri', role: 'admin', serverMode: true };
const ANON: Viewer = { authenticated: false, username: '', role: 'viewer', serverMode: true };

function json(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

const ROWS: RdRow[] = [
  { id: 1, ngayAir: '12/10/2026', nguoiAir: 'Phát', channel: 'kenh6', link: 'https://www.tiktok.com/@kenh6/video/7400000000000015838', partners: ['Cafe Mây'], views: 349, likes: 0, comments: null, saves: null, shares: 0, updatedAt: '28/10/2026-12:30', status: 'Success' },
  { id: 2, ngayAir: '13/10/2026', channel: '', link: 'https://www.tiktok.com/@kenh27/photo/7400000000000071271', partners: ['Spa', 'Cafe Mây'], views: null, likes: null, comments: null, saves: null, shares: null, updatedAt: '', status: 'Error: Không đọc được số liệu' },
];

let calls: Array<{ url: string; init?: RequestInit }> = [];
const sent: Array<Record<string, unknown>> = [];

function renderAs(viewer: Viewer, adminPage: boolean) {
  resetAppState({
    viewer,
    storage: 'postgres',
    proxyCount: 1,
    rd: {
      months: { tiktok: [{ thang: '2026-10', label: 'Tháng 10/2026', rows: 2 }], threads: null },
      thang: { tiktok: '2026-10', threads: '' },
      table: { state: 'ready', platform: 'tiktok', thang: '2026-10', partners: ['Cafe Mây', 'Spa'], rows: ROWS },
    },
  });
  resetRiviuDataModule();
  installRiviuDataHooks();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RiviuDataLayout adminPage={adminPage} /></QueryClientProvider>);
}

beforeEach(() => {
  calls = [];
  sent.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url.startsWith('/riviu-data/months')) return json({ months: [{ thang: '2026-10', label: 'Tháng 10/2026', rows: 2 }], defaultThang: '2026-10' });
    if (url.startsWith('/riviu-data/rows')) return json({ partners: ['Cafe Mây', 'Spa'], rows: ROWS });
    if (url.startsWith('/riviu-data/report-posters')) {
      return json({ posters: [{ name: 'Phát', linkCount: 1, rawLinkCount: 1 }, { name: 'Chưa rõ người đăng', linkCount: 0, rawLinkCount: 1 }] });
    }
    if (url.startsWith('/riviu-data/report-partners')) {
      return json({ partners: [{ name: 'Spa', linkCount: 0, rawLinkCount: 1 }, { name: 'Cafe Mây', linkCount: 1, rawLinkCount: 1 }] });
    }
    if (url === '/riviu-data/export-report') {
      return new Response(new Blob(['xlsx']), { status: 200, headers: { 'Content-Disposition': "attachment; filename*=UTF-8''Cafe%20M%C3%A2y%202026-10.xlsx" } });
    }
    if (url.startsWith('/schedule')) {
      if (init?.method === 'POST') return json({ success: true, schedule: JSON.parse(String(init.body)), runs: [], platforms: { tiktok: { nextRun: '2026-10-29T08:30:00+07:00' } } });
      return json({
        schedule: { version: 1, platforms: { tiktok: { slots: [{ time: '21:00', enabled: true }], options: { workers: 10, scrapeMode: 'request', useProxy: false, useSession: false, retryFailed: true } } } },
        runs: [{ id: 'a', platform: 'tiktok', slot: '21:00', startedAt: '2026-10-27T21:00:02+07:00', status: 'completed', message: 'Đã quét xong.', counts: { total: 10, success: 9, error: 1 } }],
        platforms: { tiktok: { nextRun: '2026-10-28T21:00:00+07:00' } }, timezone: 'Asia/Ho_Chi_Minh (UTC+7)',
      });
    }
    return json({ success: true });
  }));
  setSocketForTests({ readyState: 1, send: (text: string) => sent.push(JSON.parse(text)) } as unknown as WebSocket);
});

afterEach(() => {
  setSocketForTests(null);
  vi.unstubAllGlobals();
});

const ctx = { runId: 'run-1', platform: 'tiktok', fileId: 'riviu-data', sheetName: '2026-10' };

describe('public viewer page (/)', () => {
  it('shows the month report read-only, with a login link and no admin controls', () => {
    renderAs(ANON, false);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Tháng 10/2026');
    expect(screen.getByTestId('month-select')).toHaveTextContent('Tháng 10/2026');
    expect(screen.getByTestId('login-link')).toHaveAttribute('href', '/admin');
    expect(screen.getByTestId('open-export')).toBeEnabled();
    for (const id of ['scan-toolbar', 'start-scan', 'settings-menu', 'logout']) expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hẹn giờ tự động' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Hướng dẫn/)).not.toBeInTheDocument();
    expect(screen.getByTestId('last-update')).toHaveTextContent('Số liệu cập nhật lúc 12:30, 28/10/2026');
  });

  it('renders unknown metrics blank, confirmed zeros as 0, and totals of known values only', () => {
    renderAs(ANON, false);
    const [first, second] = screen.getAllByTestId('link-row');
    expect(within(first).getByText('OK')).toBeInTheDocument();
    expect(first.querySelector('[data-metric="likes"]')).toHaveTextContent(/^0$/);
    expect(first.querySelector('[data-metric="comments"]')).toHaveTextContent(/^$/);
    for (const cell of second.querySelectorAll('[data-metric]')) expect(cell).toHaveTextContent(/^$/);
    expect(screen.getByTestId('total-views')).toHaveTextContent('349');
    expect(screen.getByTestId('total-likes')).toHaveTextContent(/^0$/);
    expect(screen.getByTestId('total-comments')).toHaveTextContent(/^$/);
  });

  it('filters by partner and search, totals follow the filter', async () => {
    renderAs(ANON, false);
    await userEvent.click(screen.getByRole('combobox', { name: 'Lọc theo đối tác' }));
    await userEvent.type(screen.getByLabelText('Tìm đối tác…'), 'spa');
    expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['Spa1']);
    await userEvent.click(screen.getByRole('option', { name: /Spa/ }));
    expect(screen.getAllByTestId('link-row')).toHaveLength(1);
    expect(screen.getByTestId('row-count')).toHaveTextContent('1 / 2');
    expect(screen.getByTestId('total-views')).toHaveTextContent(/^$/);
    await userEvent.click(screen.getByRole('combobox', { name: 'Lọc theo đối tác' }));
    await userEvent.click(screen.getByRole('option', { name: /Tất cả đối tác/ }));
    await userEvent.type(screen.getByLabelText('Tìm trong danh sách link'), 'kenh6');
    expect(screen.getAllByTestId('link-row')).toHaveLength(1);
  });

  it('filters by Người đăng, every poster by default', async () => {
    renderAs(ANON, false);
    expect(screen.getByRole('combobox', { name: 'Lọc theo người đăng' })).toHaveTextContent('Tất cả người đăng');
    await userEvent.click(screen.getByRole('combobox', { name: 'Lọc theo người đăng' }));
    expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['Tất cả người đăng2', 'Phát1', 'Chưa rõ người đăng1']);
    await userEvent.click(screen.getByRole('option', { name: /Chưa rõ/ }));
    expect(screen.getAllByTestId('link-row')).toHaveLength(1);
    expect(screen.getByTestId('row-count')).toHaveTextContent('1 / 2');
  });

  it('has no refresh button, and a back-to-top button once scrolled down', async () => {
    const scrollTo = vi.fn();
    vi.stubGlobal('scrollTo', scrollTo);
    renderAs(ANON, false);
    // The numbers change only when a scan ends, and the page reloads them then by itself.
    expect(screen.queryByRole('button', { name: 'Làm mới' })).not.toBeInTheDocument();
    const button = screen.getByTestId('back-to-top');
    expect(button).toHaveAttribute('aria-hidden', 'true');
    act(() => {
      Object.defineProperty(window, 'scrollY', { value: 1500, configurable: true });
      window.dispatchEvent(new Event('scroll'));
    });
    expect(button).toHaveAttribute('aria-hidden', 'false');
    await userEvent.click(button);
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });
    Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
  });

  it('shows staff the Báo cáo người đăng tab too', async () => {
    renderAs(ANON, false);
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Danh sách link', 'Báo cáo người đăng', 'Tổng kết']);
    await userEvent.click(screen.getByRole('tab', { name: 'Báo cáo người đăng' }));
    expect(screen.getAllByTestId('poster-row')).toHaveLength(2);
  });

  it('shows partner names in full, up to three, then +N', () => {
    renderAs(ANON, false);
    const second = screen.getAllByTestId('link-row')[1];
    expect([...within(second).getByTestId('partner-chips').children].map(chip => chip.textContent)).toEqual(['Spa', 'Cafe Mây']);
    act(() => appStore.set(state => ({ rd: { ...state.rd, table: { state: 'ready', platform: 'tiktok', thang: '2026-10', partners: [], rows: [
      { ...ROWS[0], partners: ['Một', 'Hai', 'Ba', 'Bốn', 'Năm'] },
    ] } } })));
    expect([...screen.getByTestId('partner-chips').children].map(chip => chip.textContent)).toEqual(['Một', 'Hai', 'Ba', '+2']);
  });

  /** Renders with only the min-width queries up to `width` matching. */
  function atWidth(width: number, run: () => void) {
    const real = window.matchMedia;
    window.matchMedia = ((query: string) => {
      const min = /min-width:\s*(\d+)px/.exec(query);
      return { ...real(query), matches: min ? Number(min[1]) <= width : false };
    }) as typeof window.matchMedia;
    try {
      run();
    } finally {
      window.matchMedia = real;
    }
  }

  it('gives Người đăng and Cập nhật room to Đối tác below 1280px', () => {
    atWidth(1100, () => {
      renderAs(ANON, false);
      const headers = screen.getAllByRole('columnheader').map(cell => cell.textContent);
      expect(headers).toContain('Đối tác');
      expect(headers).not.toContain('Người đăng');
      expect(headers).not.toContain('Cập nhật');
    });
  });

  it('shows phones cards instead of tables, with the month totals', () => {
    atWidth(390, () => {
      renderAs(ANON, false);
      expect(screen.queryByTestId('link-table')).not.toBeInTheDocument();
      const cards = screen.getAllByTestId('link-card');
      expect(cards).toHaveLength(2);
      expect(within(cards[0]).getByText('kenh6')).toBeInTheDocument();
      expect(cards[0].querySelector('[data-metric="views"]')).toHaveTextContent('349');
      // Unknown stays blank (a dash), never 0.
      expect(cards[1].querySelector('[data-metric="views"]')).toHaveTextContent('–');
      expect(screen.getByTestId('total-views')).toHaveTextContent('349');
    });
  });

  it('has no Lịch sử quét button for staff', () => {
    renderAs(ANON, false);
    expect(screen.queryByRole('button', { name: 'Lịch sử quét' })).not.toBeInTheDocument();
  });

  it('labels links by kind and post id, and a short link by the post it led to', () => {
    renderAs(ANON, false);
    expect(screen.getAllByTestId('post-link').map(link => link.textContent)).toEqual(['tiktok.com/@kenh6/video/7400000000000015838', 'tiktok.com/@kenh27/photo/7400000000000071271']);
    act(() => appStore.set(state => ({ rd: { ...state.rd, table: { state: 'ready', platform: 'tiktok', thang: '2026-10', partners: [], rows: [
      { ...ROWS[0], link: 'https://vt.tiktok.com/ZSxQ4KHvk/', resolvedLink: 'https://www.tiktok.com/@kenh6/photo/7644157834795519253' },
      { ...ROWS[1], id: 3, link: 'https://vm.tiktok.com/ZP8GTPPoA/' },
    ] } } })));
    const [resolved, unresolved] = screen.getAllByTestId('link-row');
    expect(within(resolved).getByTestId('post-link')).toHaveTextContent('tiktok.com/@kenh6/photo/7644157834795519253');
    expect(within(resolved).getByTestId('post-link')).toHaveAttribute('href', 'https://vt.tiktok.com/ZSxQ4KHvk/');
    expect(within(resolved).getByText('Ảnh')).toBeInTheDocument();
    expect(within(unresolved).getByTestId('post-link')).toHaveTextContent('vm.tiktok.com/ZP8GTPPoA');
    expect(within(unresolved).getByText('Link gọn')).toBeInTheDocument();
  });

  it('shows the per-partner Tổng kết to visitors, with month totals counting each link once', async () => {
    renderAs(ANON, false);
    await userEvent.click(screen.getByRole('tab', { name: /Tổng kết/ }));
    const rows = screen.getAllByTestId('summary-row');
    // Sorted by views (most first); a partner whose links are all unknown sinks to the bottom.
    expect(rows.map(item => within(item).getAllByRole('cell')[1].textContent)).toEqual(['Cafe Mây', 'Spa']);
    expect(rows[0].querySelector('[data-metric="links"]')).toHaveTextContent('2');
    expect(rows[0].querySelector('[data-metric="views"]')).toHaveTextContent('349');
    expect(rows[1].querySelector('[data-metric="views"]')).toHaveTextContent(/^$/);
    expect(screen.getByTestId('summary-total-links')).toHaveTextContent('2');
    expect(screen.getByTestId('summary-total-views')).toHaveTextContent('349');
    await userEvent.type(screen.getByLabelText('Tìm đối tác trong tổng kết'), 'spa');
    expect(screen.getAllByTestId('summary-row')).toHaveLength(1);
    expect(screen.getByTestId('summary-count')).toHaveTextContent('1 / 2');
  });

  it('gives the admin the same Tổng kết tab', async () => {
    renderAs(ADMIN, true);
    await userEvent.click(screen.getByRole('tab', { name: /Tổng kết/ }));
    expect(screen.getByTestId('summary-table')).toBeInTheDocument();
    expect(screen.getAllByTestId('summary-row')).toHaveLength(2);
  });

  it('shows Người đăng, the post type and an exclusive tag for a single partner', () => {
    renderAs(ANON, false);
    expect(screen.getAllByRole('columnheader').map(cell => cell.textContent)).toEqual([
      'Ngày đăng', 'Người đăng', 'Tên kênh', 'Link', 'Lượt xem', 'Tim', 'Bình luận', 'Lượt lưu', 'Chia sẻ', 'Đối tác', 'Trạng thái', 'Cập nhật',
    ]);
    const [first, second] = screen.getAllByTestId('link-row');
    expect(within(first).getByText('Phát')).toBeInTheDocument();
    expect(within(first).getByText('Video')).toBeInTheDocument();
    expect(within(first).getByTestId('exclusive-tag')).toHaveTextContent('Độc quyền');
    expect(within(second).getByText('Ảnh')).toBeInTheDocument();
    expect(within(second).queryByTestId('exclusive-tag')).not.toBeInTheDocument();
  });

  it('opens a post link on double click and copies it with Ctrl + click', async () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderAs(ANON, false);
    const link = screen.getAllByTestId('post-link')[0];
    await userEvent.click(link);
    expect(open).not.toHaveBeenCalled();
    expect(link).toHaveFocus();
    await userEvent.dblClick(link);
    expect(open).toHaveBeenCalledWith(ROWS[0].link, '_blank', 'noopener,noreferrer');
    fireEvent.click(link, { ctrlKey: true });
    expect(writeText).toHaveBeenCalledWith(ROWS[0].link);
  });

  it('shows a calm progress line while a scan runs, and the retry time during the cooldown', () => {
    renderAs(ANON, false);
    expect(screen.queryByTestId('scan-state')).not.toBeInTheDocument();
    act(() => handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', total: 2, processed: 1 } }));
    expect(screen.getByTestId('scan-state')).toHaveTextContent('Đang cập nhật số liệu…');
    act(() => handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', retryPass: true, retryWaitSeconds: 300, total: 2, processed: 2, success: 1, retryCount: 1 } }));
    expect(screen.getByTestId('scan-state')).toHaveTextContent(/^Số liệu đã cập nhật\. Hệ thống sẽ tự quét lại vài link chưa đọc được lúc \d\d:\d\d\.$/);
    expect(screen.queryByTestId('skip-retry')).not.toBeInTheDocument();
    act(() => handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'completed', done: true, total: 2, processed: 2 } }));
    expect(screen.queryByTestId('scan-state')).not.toBeInTheDocument();
  });

  it('exports the chosen partners of the month with the server filename', async () => {
    const createObjectURL = vi.fn(() => 'blob:x');
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.download); });
    renderAs(ANON, false);
    act(() => openModal('export'));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Cafe Mây')).toBeInTheDocument();
    expect(within(dialog).getByText('1 link')).toBeInTheDocument();
    expect(within(dialog).getByText(/chưa có số liệu hoặc dưới ngưỡng view/)).toBeInTheDocument();
    expect(calls.find(call => call.url.startsWith('/riviu-data/report-partners'))?.url).toBe('/riviu-data/report-partners?platform=tiktok&thang=2026-10&apply_min_views=true&min_views=100');
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'Cafe Mây' }));
    await userEvent.click(within(dialog).getByTestId('export-report'));
    await vi.waitFor(() => expect(clicked).toEqual(['Cafe Mây 2026-10.xlsx']));
    const exportCall = calls.find(call => call.url === '/riviu-data/export-report');
    expect(JSON.parse(String(exportCall?.init?.body))).toEqual({ platform: 'tiktok', thang: '2026-10', partners: ['Cafe Mây'], applyMinViews: true, minViews: 100 });
    click.mockRestore();
  });

  it("switches platform and partner/poster tabs inside the export dialog, keeping each tab's ticks", async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderAs(ANON, false);
    act(() => openModal('export'));
    const dialog = screen.getByRole('dialog');
    expect(within(within(dialog).getByTestId('export-platform')).getAllByRole('tab').map(tab => tab.textContent)).toEqual(['TikTok', 'Threads']);
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: 'Cafe Mây' }));
    await userEvent.click(within(dialog).getByRole('tab', { name: /Người đăng/ }));
    await userEvent.click(await within(dialog).findByRole('checkbox', { name: 'Phát' }));
    expect(calls.some(call => call.url === '/riviu-data/report-posters?platform=tiktok&thang=2026-10&apply_min_views=true&min_views=100')).toBe(true);
    // Back and forth: each tab still has what was ticked in it.
    await userEvent.click(within(dialog).getByRole('tab', { name: /^Đối tác$/ }));
    expect(await within(dialog).findByRole('checkbox', { name: 'Cafe Mây' })).toBeChecked();
    expect(within(dialog).getByRole('checkbox', { name: 'Spa' })).not.toBeChecked();
    await userEvent.click(within(dialog).getByRole('tab', { name: /Người đăng/ }));
    expect(await within(dialog).findByRole('checkbox', { name: 'Phát' })).toBeChecked();
    await userEvent.click(within(dialog).getByTestId('export-report'));
    await vi.waitFor(() => expect(calls.some(call => call.url === '/riviu-data/export-report')).toBe(true));
    const body = JSON.parse(String(calls.find(call => call.url === '/riviu-data/export-report')?.init?.body));
    expect(body).toEqual({ platform: 'tiktok', thang: '2026-10', by: 'poster', posters: ['Phát'], applyMinViews: true, minViews: 100 });
    click.mockRestore();
  });
});

describe('admin page (/admin)', () => {
  it('has the scan toolbar, schedule, settings and a logout button', () => {
    renderAs(ADMIN, true);
    expect(screen.getByTestId('scan-toolbar')).toBeInTheDocument();
    expect(screen.getByTestId('start-scan')).toHaveTextContent('Quét tháng 10/2026');
    expect(screen.getByRole('button', { name: 'Hẹn giờ tự động' })).toBeInTheDocument();
    expect(screen.getByTestId('settings-menu')).toBeInTheDocument();
    expect(screen.getByTestId('logout')).toHaveTextContent('Đăng xuất');
    expect(screen.queryByTestId('login-link')).not.toBeInTheDocument();
  });

  it('saves a toolbar change on the server', async () => {
    renderAs(ADMIN, true);
    fireEvent.change(screen.getByLabelText(/Luồng/), { target: { value: '20' } });
    await vi.waitFor(() => expect(calls.some(call => call.url === '/scan-settings' && call.init?.method === 'POST')).toBe(true));
    const body = JSON.parse(String(calls.find(call => call.url === '/scan-settings')?.init?.body));
    expect(body.platforms.tiktok.workers).toBe(20);
    expect(appStore.get().scanSettings.tiktok.workers).toBe(20);
  });

  it('starts a month scan and offers "Bỏ qua quét lại" during the cooldown', async () => {
    renderAs(ADMIN, true);
    await userEvent.click(screen.getByTestId('start-scan'));
    expect(sent[0]).toMatchObject({ action: 'start', platform: 'tiktok', sheet_name: '2026-10', retry_failed: true });
    expect(screen.getByTestId('start-scan')).toBeDisabled();
    act(() => handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'running', retryPass: true, retryWaitSeconds: 300, total: 2, processed: 2, success: 1, retryCount: 1 } }));
    expect(screen.getByTestId('progress-text')).toHaveTextContent(/Chờ quét lại 1 link lỗi lúc \d\d:\d\d \(còn [45]:\d\d\)/);
    await userEvent.click(screen.getByTestId('skip-retry'));
    expect(sent.at(-1)).toEqual({ action: 'skip_retry', platform: 'tiktok' });
    expect(screen.getByTestId('skip-retry')).toBeDisabled();
    act(() => handleSocketMessage({ type: 'status', ...ctx, data: { phase: 'completed', done: true, total: 2, processed: 2, success: 1, error: 1 } }));
    expect(screen.queryByTestId('skip-retry')).not.toBeInTheDocument();
    expect(screen.getByTestId('start-scan')).toBeEnabled();
  });

  it('edits and saves the automatic schedule', async () => {
    renderAs(ADMIN, true);
    act(() => openModal('schedule'));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('21:00', { selector: 'span' })).toBeInTheDocument();
    expect(within(dialog).getByTestId('next-run')).toHaveTextContent('21:00 · 28/10');
    expect(within(dialog).getByText('9/10 thành công · 1 lỗi')).toBeInTheDocument();
    expect(within(dialog).getByTestId('save-schedule')).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Giờ mới'), { target: { value: '08:30' } });
    await userEvent.click(within(dialog).getByRole('button', { name: /Thêm giờ/ }));
    // The 21:00 slot also scans October 2026, besides the newest month.
    await userEvent.click(within(dialog).getByRole('button', { name: 'Chọn tháng cho 21:00' }));
    await userEvent.click(await screen.findByRole('checkbox', { name: /Tháng 10\/2026/ }));
    expect(within(dialog).getByTestId('slot-months-21:00')).toHaveTextContent('Tháng mới nhấtTháng 10/2026');
    await userEvent.keyboard('{Escape}');
    await userEvent.click(within(dialog).getByTestId('save-schedule'));
    await vi.waitFor(() => expect(calls.some(call => call.url === '/schedule' && call.init?.method === 'POST')).toBe(true));
    const body = JSON.parse(String(calls.find(call => call.init?.method === 'POST' && call.url === '/schedule')?.init?.body));
    expect(body.platforms.tiktok.slots).toEqual([{ time: '08:30', enabled: true, months: ['latest'] }, { time: '21:00', enabled: true, months: ['latest', '2026-10'] }]);
    expect(body.platforms.threads.options.scrapeMode).toBe('hybrid');
  });

  it("reports the month by poster, with each poster's link count, unknown posters last", async () => {
    renderAs(ADMIN, true);
    const tabs = screen.getAllByRole('tab').map(tab => tab.textContent?.replace(/\d+$/, ''));
    expect(tabs.indexOf('Báo cáo người đăng')).toBe(tabs.indexOf('Tổng kết') - 1);
    await userEvent.click(screen.getByRole('tab', { name: /Báo cáo người đăng/ }));
    const table = screen.getByTestId('poster-table');
    expect(within(table).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual([
      'Ngày đăng', 'Người đăng', 'Link', 'Lượt xem', 'Tim', 'Bình luận', 'Lượt lưu', 'Chia sẻ', 'Đối tác', 'Trạng thái', 'Tổng link', 'Cập nhật',
    ]);
    const rows = screen.getAllByTestId('poster-row');
    expect(rows.map(row => (row as HTMLTableRowElement).cells[1].textContent)).toEqual(['Phát', 'Chưa rõ người đăng']);
    expect(screen.getAllByTestId('poster-links').map(cell => cell.textContent)).toEqual(['1', '1']);
    expect(screen.getByTestId('poster-count')).toHaveTextContent('2 người · 2 link');
    expect(screen.getByTestId('poster-total-links')).toHaveTextContent('2');
  });

  it("shows a reloaded page the last scan's logs once, and how much of the scan is listed", async () => {
    renderAs(ADMIN, true);
    const log = { type: 'log', ...ctx, message: 'Bắt đầu quét 900 link', seq: 7001, at: '2026-10-28T02:00:05+00:00' };
    const session = {
      ...ctx, running: false,
      status: { ...ctx, phase: 'completed', done: true, total: 900, processed: 900, success: 900 },
      results: [{ ...ctx, id: 1, url: ROWS[0].link, status: 'Success', views: 349 }],
      logs: [log],
    };
    act(() => handleSocketMessage({ type: 'session', ...ctx, data: session }));
    // A reconnect sends the same snapshot again: its lines are not repeated.
    act(() => handleSocketMessage({ type: 'session', ...ctx, data: session }));
    act(() => handleSocketMessage(log));
    expect(appStore.get().logs.filter(line => line.message === 'Bắt đầu quét 900 link')).toHaveLength(1);
    // The run scanned 900 links; the server kept the newest results only.
    await userEvent.click(screen.getByRole('tab', { name: /Kết quả quét/ }));
    expect(screen.getByTestId('count-live')).toHaveTextContent('900');
    expect(screen.getByTestId('last-scan-note')).toHaveTextContent('Lần quét gần nhất: Tháng 10/2026 · 900 link đã quét · dưới đây là 1 kết quả mới nhất');
  });

  it("gives another month's last scan no count", () => {
    renderAs(ADMIN, true);
    const other = { ...ctx, runId: 'run-9', sheetName: '2026-09' };
    act(() => handleSocketMessage({ type: 'session', ...other, data: { ...other, running: false, status: { ...other, phase: 'completed', done: true, processed: 500 }, results: [] } }));
    // The page opens on the last scan's month; on any other month that scan has no count.
    expect(screen.getByTestId('count-live')).toHaveTextContent('500');
    act(() => { void selectThang('2026-10'); });
    expect(screen.queryByTestId('count-live')).not.toBeInTheDocument();
  });
});
