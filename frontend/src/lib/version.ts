// GET /api/version: which storage the backend uses decides between the workbook UI and the
// Riviu Data (Postgres, by month) UI.
import type { StorageKind } from '@/state/app';

export interface BackendVersion {
  storage: StorageKind;
  maxWorkers: number;
}

export function parseVersion(payload: unknown): BackendVersion {
  const data = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
  return {
    storage: data.storage === 'postgres' ? 'postgres' : 'workbook',
    maxWorkers: Number(data.maxWorkers) || 50,
  };
}

export async function fetchVersion(): Promise<BackendVersion> {
  const response = await fetch('/api/version', { cache: 'no-store', credentials: 'same-origin' });
  // An older backend without the route keeps the workbook UI.
  if (response.status === 404) return parseVersion({});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return parseVersion(await response.json());
}

/** `/admin` shows the admin page; source mode (no login) has only `/`, where the admin works. */
export function isAdminPage(pathname: string, viewer: { serverMode: boolean; role: string }): boolean {
  if (/^\/admin\/?$/.test(pathname)) return viewer.role === 'admin';
  return !viewer.serverMode && viewer.role === 'admin';
}
