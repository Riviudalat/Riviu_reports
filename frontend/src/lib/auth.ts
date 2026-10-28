// Who is using the app. GET /auth-status answers
// {"authenticated": bool, "user": {"username": str, "role": "admin" | "viewer"} | null}.
// Source mode has no login: the route is missing (404), and the user is treated as admin.
import { z } from 'zod';
import { apiFetch, ApiError, bootstrapLocalSession } from './api';

export type Role = 'admin' | 'viewer';

export interface Viewer {
  authenticated: boolean;
  username: string;
  role: Role;
  /** True when the backend has a login (server mode); false in source mode. */
  serverMode: boolean;
}

const authStatusSchema = z.object({
  authenticated: z.boolean(),
  user: z
    .object({
      username: z.string().default(''),
      role: z.enum(['admin', 'viewer']).catch('viewer'),
    })
    .nullish(),
});

export const SOURCE_MODE_VIEWER: Viewer = { authenticated: true, username: '', role: 'admin', serverMode: false };

export function parseAuthStatus(payload: unknown): Viewer {
  const parsed = authStatusSchema.safeParse(payload);
  if (!parsed.success) return UNKNOWN_VIEWER;
  const { authenticated, user } = parsed.data;
  if (!authenticated) return { authenticated: false, username: '', role: 'viewer', serverMode: true };
  // The shared-password backend (before roles) answers without a user: it only had admins.
  if (!user) return { authenticated: true, username: '', role: 'admin', serverMode: true };
  return { authenticated: true, username: user.username, role: user.role, serverMode: true };
}

export async function fetchViewer(): Promise<Viewer> {
  let response: Response;
  // The Vite dev server does not serve the backend's / page, which issues the local session cookie.
  if (import.meta.env.DEV) await bootstrapLocalSession();
  try {
    response = await apiFetch('/auth-status', { cache: 'no-store', quietForbidden: true });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { authenticated: false, username: '', role: 'viewer', serverMode: true };
    return UNKNOWN_VIEWER;
  }
  // Source mode: the login routes answer 404.
  if (response.status === 404) return SOURCE_MODE_VIEWER;
  if (!response.ok) return UNKNOWN_VIEWER;
  try {
    return parseAuthStatus(await response.json());
  } catch {
    return UNKNOWN_VIEWER;
  }
}

/** Least privilege when the role cannot be read (backend down or answering garbage). */
export const UNKNOWN_VIEWER: Viewer = { authenticated: true, username: '', role: 'viewer', serverMode: true };

export function isAdmin(viewer: Pick<Viewer, 'role'>): boolean {
  return viewer.role === 'admin';
}

export const ROLE_LABELS: Record<Role, string> = { admin: 'Quản trị', viewer: 'Nhân viên' };
