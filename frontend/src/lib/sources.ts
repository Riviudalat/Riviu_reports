// Per-platform source slots and the persisted source preferences, ported from app.js.
import { PLATFORM_KEYS, type PlatformKey } from './platforms';

export interface SourceSlot {
  fileId: string;
  label: string;
  displaySheet: string;
  scanSheet: string;
  pushSheet: string;
  sheets: string[];
  hiddenSheets: string[];
  url: string;
  /** The URL field was edited by the user; do not overwrite it from the server. */
  dirty: boolean;
}

export type PersistedSource = Pick<SourceSlot, 'fileId' | 'displaySheet' | 'scanSheet' | 'pushSheet' | 'url'>;
export type SourcePreferences = Record<PlatformKey, PersistedSource>;

export function emptySource(): SourceSlot {
  return { fileId: '', label: '', displaySheet: '', scanSheet: '', pushSheet: '', sheets: [], hiddenSheets: [], url: '', dirty: false };
}

const EXCEL_SHEET_NAME_MAX = 31;

export function validatedSourceItem(item: unknown = {}): PersistedSource {
  if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Nguồn đã lưu không hợp lệ.');
  const record = item as Record<string, unknown>;
  const source = {} as PersistedSource;
  for (const key of ['fileId', 'displaySheet', 'scanSheet', 'pushSheet', 'url'] as const) {
    const value = record[key] ?? '';
    // eslint-disable-next-line no-control-regex
    if (typeof value !== 'string' || /[\x00-\x1f\x7f]/.test(value)) throw new Error('Nguồn đã lưu không hợp lệ.');
    source[key] = key === 'url' ? value.trim() : value;
  }
  if (source.fileId.length > 1024 || /^(?:[\\/]|[A-Za-z]:)/.test(source.fileId)
    || source.fileId.split(/[\\/]/).some(part => ['.', '..'].includes(part))) throw new Error('Tên file đã lưu không hợp lệ.');
  for (const key of ['displaySheet', 'scanSheet', 'pushSheet'] as const) {
    if (source[key].length > EXCEL_SHEET_NAME_MAX || /[\\/?*[\]:]/.test(source[key])) throw new Error('Tên sheet đã lưu không hợp lệ.');
  }
  if (source.url) {
    const url = new URL(source.url);
    const path = url.pathname.match(/^\/spreadsheets\/d\/([A-Za-z0-9_-]+)(?:\/(?:edit|view|copy))?\/?$/);
    if (source.url.length >= 4096 || url.protocol !== 'https:' || url.hostname !== 'docs.google.com' || url.port || url.username || url.password || !path) {
      throw new Error('URL Google Sheet đã lưu không hợp lệ.');
    }
    const gid = url.searchParams.get('gid') || new URLSearchParams(url.hash.slice(1)).get('gid');
    source.url = `https://docs.google.com/spreadsheets/d/${path[1]}/edit${gid && /^\d+$/.test(gid) ? `#gid=${gid}` : ''}`;
  }
  return source;
}

export function validatedSourcePreferences(sources: unknown): SourcePreferences {
  if (!sources || typeof sources !== 'object' || Array.isArray(sources)) throw new Error('Nguồn đã lưu không hợp lệ.');
  const record = sources as Record<string, unknown>;
  // Like the backend, a platform missing from saved preferences simply has no source yet.
  return Object.fromEntries(PLATFORM_KEYS.map(platform => [platform, validatedSourceItem(record[platform] ?? {})])) as SourcePreferences;
}

/** Only files, sheets and valid Google targets persist: never cookies, proxies, labels or runtime state. */
export function sourcePreferencesSnapshot(slots: Record<PlatformKey, SourceSlot>): SourcePreferences {
  const sources = {} as Record<PlatformKey, PersistedSource>;
  for (const platform of PLATFORM_KEYS) {
    const { fileId, displaySheet, scanSheet, pushSheet, url } = slots[platform];
    const source = { fileId, displaySheet, scanSheet, pushSheet, url };
    // A partially typed/invalid URL is UI state, not a persistable Google target.
    try { source.url = validatedSourceItem(source).url; } catch { source.url = ''; }
    sources[platform] = source;
  }
  return validatedSourcePreferences(sources);
}

/**
 * Query string for every read of a platform's workbook. file_id is always present (possibly
 * empty): without it the server answers with its legacy global selection, which may be another
 * platform's workbook.
 */
export function sourceQuery(platform: PlatformKey, fileId: string, extra: Record<string, string> = {}): URLSearchParams {
  return new URLSearchParams({ ...extra, file_id: fileId, platform });
}
