// HTTP access to the Riviu backend. Every call goes through apiFetch so that:
// - 401 (server mode, login expired or revoked) sends the browser to /login;
// - 403 for a missing local session cookie (source mode, server restarted) reloads the
//   session from GET / once and retries;
// - any other 403 (role not allowed) surfaces as a Vietnamese toast.
import { toast } from 'sonner';

export const LOCAL_SESSION_ERROR = 'Mở lại trang ứng dụng để kết nối phiên local';
export const FORBIDDEN_TEXT = 'Bạn không có quyền thực hiện thao tác này.';

export class ApiError extends Error {
  status: number;
  data: Record<string, unknown>;
  constructor(message: string, status: number, data: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export interface ApiOptions extends RequestInit {
  /** Do not toast a 403 (background reads that have their own fallback). */
  quietForbidden?: boolean;
}

let loginRedirect: (path: string) => void = path => window.location.assign(path);

/** Test seam: replace the navigation used for 401 responses. */
export function setLoginRedirect(fn: (path: string) => void) {
  loginRedirect = fn;
}

async function readErrorBody(response: Response): Promise<Record<string, unknown>> {
  try {
    const data = await response.clone().json();
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

/** Load / so the backend issues a fresh per-process session cookie (source mode). */
export async function bootstrapLocalSession(): Promise<boolean> {
  try {
    const response = await fetch('/', { cache: 'no-store', credentials: 'same-origin' });
    return response.ok;
  } catch {
    return false;
  }
}

export async function apiFetch(path: string, options: ApiOptions = {}): Promise<Response> {
  const { quietForbidden, ...init } = options;
  const send = () => fetch(path, { credentials: 'same-origin', ...init });
  let response = await send();
  if (response.status === 403) {
    const data = await readErrorBody(response);
    if (data.error === LOCAL_SESSION_ERROR && (await bootstrapLocalSession())) {
      response = await send();
    }
  }
  if (response.status === 401) {
    loginRedirect('/login');
    throw new ApiError('Cần đăng nhập', 401);
  }
  if (response.status === 403) {
    const data = await readErrorBody(response);
    if (data.error !== LOCAL_SESSION_ERROR && !quietForbidden) {
      toast.error(FORBIDDEN_TEXT, { description: typeof data.error === 'string' ? data.error : undefined });
    }
    throw new ApiError(typeof data.error === 'string' ? data.error : FORBIDDEN_TEXT, 403, data);
  }
  return response;
}

async function parseJson(response: Response): Promise<Record<string, unknown>> {
  try {
    const data = await response.json();
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

/** Fetch JSON and return it with the response; never throws for non-2xx (callers decide). */
export async function requestJson<T = Record<string, unknown>>(path: string, options: ApiOptions = {}): Promise<{ response: Response; data: T }> {
  const response = await apiFetch(path, options);
  const data = (await parseJson(response)) as T;
  return { response, data };
}

export function getJson<T = Record<string, unknown>>(path: string, options: ApiOptions = {}) {
  return requestJson<T>(path, { ...options, method: 'GET' });
}

export function postJson<T = Record<string, unknown>>(path: string, body: unknown, options: ApiOptions = {}) {
  return requestJson<T>(path, {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    body: JSON.stringify(body),
  });
}

export function postForm<T = Record<string, unknown>>(path: string, form: FormData, options: ApiOptions = {}) {
  return requestJson<T>(path, { ...options, method: 'POST', body: form });
}

export function deleteJson<T = Record<string, unknown>>(path: string, options: ApiOptions = {}) {
  return requestJson<T>(path, { ...options, method: 'DELETE' });
}

export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
