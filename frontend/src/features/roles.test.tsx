import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MainLayout } from '@/App';
import { emptySource } from '@/lib/sources';
import { appStore, openModal, resetAppState, setSourceDrawer } from '@/state/app';
import type { Viewer } from '@/lib/auth';

const ADMIN: Viewer = { authenticated: true, username: 'quantri', role: 'admin', serverMode: true };
const VIEWER: Viewer = { authenticated: true, username: 'nhanvien', role: 'viewer', serverMode: true };

function renderAs(viewer: Viewer) {
  resetAppState({
    viewer,
    sourcesInitialized: true,
    files: [{ id: 'data/t.xlsx', label: 'Báo cáo T9' }],
    sources: { tiktok: { ...emptySource(), fileId: 'data/t.xlsx', label: 'Báo cáo T9', sheets: ['T9'], scanSheet: 'T9', displaySheet: 'T9' }, threads: emptySource() },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MainLayout /></QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ partners: [{ name: 'Đối tác A', linkCount: 1, rawLinkCount: 1 }], sheets: ['T9'], currentSheet: 'T9', history: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ADMIN_ONLY_BUTTONS = ['Bắt đầu quét', 'Hủy', 'Cấu hình quét', 'Cấu hình proxy'];

describe('viewer (nhân viên)', () => {
  it('sees the workspace without any scan or settings control, and a role badge', () => {
    renderAs(VIEWER);
    expect(screen.queryByTestId('scan-toolbar')).not.toBeInTheDocument();
    expect(screen.queryByTestId('start-scan')).not.toBeInTheDocument();
    expect(screen.queryByTestId('cancel-scan')).not.toBeInTheDocument();
    for (const name of ADMIN_ONLY_BUTTONS) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    expect(screen.queryByText('Proxy xoay')).not.toBeInTheDocument();
    expect(screen.queryByText(/Cookie TikTok/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tải sheet/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('role-badge')).toHaveTextContent('nhanvien');
    expect(screen.getByTestId('role-badge')).toHaveTextContent('Nhân viên');
    // Read-only navigation stays.
    expect(screen.getByRole('button', { name: 'TikTok' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Threads' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lịch sử' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Đối tác & báo cáo' })).toBeInTheDocument();
    expect(screen.getByLabelText('Sheet đang xem')).toBeInTheDocument();
  });

  it('gets a file chooser but no upload, Google or sync controls in the source drawer', () => {
    renderAs(VIEWER);
    act(() => setSourceDrawer(true));
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByTestId('file-select')).toBeInTheDocument();
    for (const text of ['Nạp file Excel', 'Nạp sheet', 'Tạo sheet', 'Nạp tài khoản dịch vụ', 'Nạp OAuth']) {
      expect(within(drawer).queryByText(text)).not.toBeInTheDocument();
    }
    expect(within(drawer).queryByRole('textbox', { name: /Link Google Sheet/ })).not.toBeInTheDocument();
    expect(within(drawer).queryByTestId('google-login')).not.toBeInTheDocument();
  });

  it('can export partner reports but not re-scan partners', async () => {
    renderAs(VIEWER);
    act(() => openModal('report'));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Đối tác A')).toBeInTheDocument();
    expect(within(dialog).getByTestId('export-report')).toBeInTheDocument();
    expect(within(dialog).queryByTestId('refresh-partners')).not.toBeInTheDocument();
  });

  it('cannot open admin dialogs even if the state asks for them', () => {
    renderAs(VIEWER);
    act(() => openModal('proxy'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => openModal('tiktokCookie'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('admin', () => {
  it('sees scan, settings and source controls', () => {
    renderAs(ADMIN);
    expect(screen.getByTestId('scan-toolbar')).toBeInTheDocument();
    expect(screen.getByTestId('start-scan')).toBeInTheDocument();
    expect(screen.getByTestId('cancel-scan')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Tải sheet/ })).toBeInTheDocument();
    act(() => setSourceDrawer(true));
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByText('Nạp file Excel')).toBeInTheDocument();
    expect(within(drawer).getAllByText('Nạp sheet')).toHaveLength(2);
    expect(within(drawer).getByTestId('google-login')).toBeInTheDocument();
    expect(appStore.get().viewer.role).toBe('admin');
  });

  it('can re-scan selected partners from the report dialog', async () => {
    renderAs(ADMIN);
    act(() => openModal('report'));
    const dialog = screen.getByRole('dialog');
    await within(dialog).findByText('Đối tác A');
    expect(within(dialog).getByTestId('refresh-partners')).toBeInTheDocument();
  });
});
