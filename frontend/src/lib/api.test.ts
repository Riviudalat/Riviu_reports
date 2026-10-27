import { afterEach, describe, expect, it, vi } from 'vitest';

const toastError = vi.fn();
vi.mock('sonner', () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }));

const { apiFetch, FORBIDDEN_TEXT, LOCAL_SESSION_ERROR, setLoginRedirect } = await import('./api');
const { parseAuthStatus } = await import('./auth');

function json(data: unknown, status: number) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockReset();
});

describe('apiFetch', () => {
  it('shows a Vietnamese toast for a forbidden (role) call', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'Chỉ quản trị viên' }, 403)));
    await expect(apiFetch('/proxy-list')).rejects.toMatchObject({ status: 403 });
    expect(toastError).toHaveBeenCalledWith(FORBIDDEN_TEXT, { description: 'Chỉ quản trị viên' });
  });

  it('stays quiet for background reads that asked for it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'x' }, 403)));
    await expect(apiFetch('/proxy-list', { quietForbidden: true })).rejects.toMatchObject({ status: 403 });
    expect(toastError).not.toHaveBeenCalled();
  });

  it('reloads the local session cookie once and retries', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ error: LOCAL_SESSION_ERROR }, 403))
      .mockResolvedValueOnce(new Response('<html></html>', { status: 200 }))
      .mockResolvedValueOnce(json({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);
    const response = await apiFetch('/list-files?file_id=&platform=tiktok');
    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls.map(call => call[0])).toEqual(['/list-files?file_id=&platform=tiktok', '/', '/list-files?file_id=&platform=tiktok']);
    expect(toastError).not.toHaveBeenCalled();
  });

  it('sends an expired login to /login', async () => {
    const redirect = vi.fn();
    setLoginRedirect(redirect);
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'Cần đăng nhập' }, 401)));
    await expect(apiFetch('/list-files')).rejects.toMatchObject({ status: 401 });
    expect(redirect).toHaveBeenCalledWith('/login');
  });
});

describe('parseAuthStatus', () => {
  it('reads the role and username', () => {
    expect(parseAuthStatus({ authenticated: true, user: { username: 'lan', role: 'viewer' } })).toMatchObject({ role: 'viewer', username: 'lan', serverMode: true });
    expect(parseAuthStatus({ authenticated: true, user: { username: 'an', role: 'admin' } })).toMatchObject({ role: 'admin' });
  });

  it('treats an unknown role as viewer and a logged-out answer as unauthenticated', () => {
    expect(parseAuthStatus({ authenticated: true, user: { username: 'x', role: 'owner' } }).role).toBe('viewer');
    expect(parseAuthStatus({ authenticated: false, user: null }).authenticated).toBe(false);
    expect(parseAuthStatus('garbage').role).toBe('viewer');
  });
});
