// Link matching and the platform view of a sheet preview, ported from app.js.
import { normalizeVietnameseKey } from './format';
import { platformConfig, type PlatformKey } from './platforms';

export type PreviewRow = Record<string, unknown> & { _singlePartner?: boolean; _videoLink?: boolean };

export function matchesPlatformLink(value: unknown, platform: PlatformKey): boolean {
  const text = String(value || '').trim();
  // eslint-disable-next-line no-control-regex
  if (!text || /[\x00-\x20\x7f\\]/.test(text)) return false;
  try {
    const url = new URL(text.startsWith('//') ? `https:${text}` : /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port) return false;
    const config = platformConfig(platform);
    return config.linkHosts.includes(url.hostname) && config.linkPath.test(url.pathname);
  } catch {
    return false;
  }
}

const PREVIEW_LINK_COLUMNS = ['link', 'link air', 'url'];
const PREVIEW_SAVED_COLUMN = 'LƯỢT LƯU';
const PREVIEW_SHARES_COLUMN = 'CHIA SẺ';

/**
 * Workbooks share the TikTok layout; a platform whose fourth metric differs shows its own
 * column (e.g. Threads REPOST) in place of LƯỢT LƯU, just before CHIA SẺ.
 */
export function previewDisplayColumns(columns: readonly string[], platform: PlatformKey): string[] {
  const metricColumn = platformConfig(platform).savedMetric.column;
  if (metricColumn === PREVIEW_SAVED_COLUMN || !columns.includes(PREVIEW_SAVED_COLUMN)) return [...columns];
  return columns
    .filter(column => column !== PREVIEW_SAVED_COLUMN && column !== metricColumn)
    .reduce<string[]>((result, column) => {
      if (column === PREVIEW_SHARES_COLUMN) result.push(metricColumn);
      result.push(column);
      return result;
    }, []);
}

export interface PlatformPreview {
  linkColumn: string | undefined;
  rows: PreviewRow[];
  columns: string[];
  /** Rows whose link column holds a link of this platform (the "Dữ liệu sheet" count). */
  linkCount: number;
}

export function platformPreview(data: { columns?: string[]; data?: PreviewRow[] }, platform: PlatformKey): PlatformPreview {
  const config = platformConfig(platform);
  const columns = data.columns || [];
  const linkColumn = columns.find(column => PREVIEW_LINK_COLUMNS.includes(normalizeVietnameseKey(column)));
  const isPlatformLink = (row: PreviewRow) => Boolean(linkColumn) && matchesPlatformLink(row[linkColumn as string], platform);
  const rows = (data.data || []).filter(row => !linkColumn || isPlatformLink(row)
    || (config.previewKeepsTotalRow && normalizeVietnameseKey(row[linkColumn]) === 'tong'));
  const linkCount = linkColumn ? rows.filter(isPlatformLink).length : 0;
  return { linkColumn, rows, columns: previewDisplayColumns(columns, platform), linkCount };
}
