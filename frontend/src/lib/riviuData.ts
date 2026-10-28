// Riviu Data (RIVIU_STORAGE=postgres): posts and metrics by month, read from /riviu-data/*
// (riviu/api/riviu_data.py). Invariant: a metric null is unknown and renders blank; 0 is a
// confirmed zero. Totals add the known values and stay blank only when nothing is known.
import { getJson } from './api';
import { metricNumber, normalizeVietnameseKey, sumKnown } from './format';
import type { PlatformKey } from './platforms';
import { isNoStatsStatus } from './scan';

export interface MonthEntry {
  /** "YYYY-MM" */
  thang: string;
  /** "Tháng 10/2026" */
  label: string;
  rows: number;
}

export interface MonthsResponse {
  months: MonthEntry[];
  defaultThang: string;
}

export interface RdRow {
  id: number | string;
  /** dd/mm/yyyy or '' */
  ngayAir: string;
  /** NGƯỜI AIR (staff who posted); '' until Riviu Data fills it. */
  nguoiAir?: string;
  channel: string;
  link: string;
  partners: string[];
  views: number | null;
  likes: number | null;
  comments: number | null;
  /** TikTok only. */
  saves?: number | null;
  /** Threads only (REPOST in place of LƯỢT LƯU). */
  reposts?: number | null;
  shares: number | null;
  /** "dd/mm/yyyy-HH:MM" or '' */
  updatedAt: string;
  status: string;
}

export type RdTable =
  | { state: 'loading' }
  | { state: 'empty' }
  | { state: 'ready'; platform: PlatformKey; thang: string; partners: string[]; rows: RdRow[] }
  | { state: 'error'; text: string };

export type MetricKey = 'views' | 'likes' | 'comments' | 'saves' | 'reposts' | 'shares';

export interface MetricColumn {
  key: MetricKey;
  label: string;
}

/** The metric columns in table order; Threads shows REPOST where TikTok shows LƯỢT LƯU. */
export function metricColumns(platform: PlatformKey): MetricColumn[] {
  return [
    { key: 'views', label: 'Lượt xem' },
    { key: 'likes', label: 'Tim' },
    { key: 'comments', label: 'Bình luận' },
    platform === 'threads' ? { key: 'reposts', label: 'Repost' } : { key: 'saves', label: 'Lượt lưu' },
    { key: 'shares', label: 'Chia sẻ' },
  ];
}

const nullableNumber = (value: unknown): number | null => metricNumber(value);

function normalizeRow(raw: unknown): RdRow | null {
  if (!raw || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  const link = String(record.link || '').trim();
  if (!link) return null;
  return {
    id: (record.id as number | string) ?? link,
    ngayAir: String(record.ngayAir || ''),
    nguoiAir: String(record.nguoiAir || ''),
    channel: String(record.channel || ''),
    link,
    partners: Array.isArray(record.partners) ? record.partners.map(String).filter(Boolean) : [],
    views: nullableNumber(record.views),
    likes: nullableNumber(record.likes),
    comments: nullableNumber(record.comments),
    ...('saves' in record ? { saves: nullableNumber(record.saves) } : {}),
    ...('reposts' in record ? { reposts: nullableNumber(record.reposts) } : {}),
    shares: nullableNumber(record.shares),
    updatedAt: String(record.updatedAt || ''),
    status: String(record.status || ''),
  };
}

export async function fetchMonths(platform: PlatformKey): Promise<MonthsResponse> {
  const { response, data } = await getJson<{ months?: unknown; defaultThang?: string; error?: string }>(
    `/riviu-data/months?platform=${encodeURIComponent(platform)}`, { cache: 'no-store', quietForbidden: true },
  );
  if (!response.ok) throw new Error(data.error || 'Không tải được danh sách tháng.');
  const months = (Array.isArray(data.months) ? data.months : [])
    .map(item => item as Record<string, unknown>)
    .filter(item => typeof item.thang === 'string' && /^\d{4}-\d{2}$/.test(item.thang))
    .map(item => ({ thang: String(item.thang), label: String(item.label || monthLabel(String(item.thang))), rows: Number(item.rows) || 0 }));
  return { months, defaultThang: String(data.defaultThang || months[0]?.thang || '') };
}

export async function fetchRows(platform: PlatformKey, thang: string): Promise<{ partners: string[]; rows: RdRow[] }> {
  const params = new URLSearchParams({ platform, thang });
  const { response, data } = await getJson<{ partners?: unknown; rows?: unknown; error?: string }>(`/riviu-data/rows?${params}`, { cache: 'no-store', quietForbidden: true });
  if (!response.ok) throw new Error(data.error || 'Không tải được danh sách link.');
  return {
    partners: Array.isArray(data.partners) ? data.partners.map(String).filter(Boolean) : [],
    rows: (Array.isArray(data.rows) ? data.rows : []).map(normalizeRow).filter((row): row is RdRow => row !== null),
  };
}

export function reportPartnersQuery(platform: PlatformKey, thang: string, applyMinViews: boolean, minViews: number): string {
  return new URLSearchParams({ platform, thang, apply_min_views: applyMinViews ? 'true' : 'false', min_views: String(minViews) }).toString();
}

export function exportBody(platform: PlatformKey, thang: string, partners: string[], applyMinViews: boolean, minViews: number) {
  return { platform, thang, partners, applyMinViews, minViews };
}

/** "2026-10" -> "Tháng 10/2026" (the server sends its own label; this is the fallback). */
export function monthLabel(thang: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(thang);
  return match ? `Tháng ${Number(match[2])}/${match[1]}` : thang;
}

// ---- Table helpers ---------------------------------------------------------------------

export interface RowFilter {
  search: string;
  partner: string;
}

export function filterRows(rows: readonly RdRow[], { search, partner }: RowFilter): RdRow[] {
  const needle = normalizeVietnameseKey(search);
  return rows.filter(row => {
    if (partner && !row.partners.includes(partner)) return false;
    if (!needle) return true;
    const text = normalizeVietnameseKey([row.channel, row.nguoiAir || '', row.link, row.partners.join(' '), row.ngayAir, row.status].join(' '));
    return text.includes(needle);
  });
}

/** Column totals over the given rows: known values only, null when nothing is known. */
export function columnTotals(rows: readonly RdRow[], platform: PlatformKey): Record<string, number | null> {
  return Object.fromEntries(metricColumns(platform).map(column => [column.key, sumKnown(rows.map(row => row[column.key]))]));
}

export type ScanStatusTone = 'success' | 'warning' | 'error' | 'muted';

/** TRẠNG THÁI QUÉT: a short label, its tone and the full status for the tooltip. */
export function scanStatusView(row: Pick<RdRow, 'status' | 'views' | 'likes' | 'comments' | 'shares'>): { label: string; tone: ScanStatusTone; title: string } {
  const status = row.status.trim();
  if (!status) return { label: 'Chưa quét', tone: 'muted', title: 'Link chưa được quét lần nào' };
  if (status === 'Success') return { label: 'OK', tone: 'success', title: 'Đọc số liệu thành công' };
  if (isNoStatsStatus(status)) return { label: status.startsWith('Partial:') ? 'Thiếu số' : 'Không số liệu', tone: 'warning', title: status };
  const detail = status.replace(/^(Error|Lỗi)\s*:\s*/i, '');
  return { label: 'Lỗi', tone: 'error', title: detail || status };
}

/** "28/10/2026-12:30" -> sortable number, or 0. */
export function updateStamp(text: string): number {
  const match = /(\d{1,2})\/(\d{1,2})\/(\d{4})\D+(\d{1,2}):(\d{2})/.exec(String(text || ''));
  if (!match) return 0;
  const [, day, month, year, hour, minute] = match.map(Number);
  return (((year * 100 + month) * 100 + day) * 100 + hour) * 100 + minute;
}

/** "28/10/2026-12:30" -> "12:30 · 28/10" for the CẬP NHẬT column. */
export function shortUpdate(text: string): string {
  const match = /(\d{1,2})\/(\d{1,2})\/(\d{4})\D+(\d{1,2}:\d{2})/.exec(String(text || ''));
  return match ? `${match[4]} · ${match[1].padStart(2, '0')}/${match[2].padStart(2, '0')}` : String(text || '');
}

/** The header's "Số liệu cập nhật lúc 12:30, 28/10/2026" from the newest row update. */
export function lastUpdateText(rows: readonly RdRow[]): string {
  let best: { stamp: number; text: string } | null = null;
  for (const row of rows) {
    const stamp = updateStamp(row.updatedAt);
    if (stamp && (!best || stamp > best.stamp)) best = { stamp, text: row.updatedAt };
  }
  if (!best) return rows.length ? 'Chưa có lần quét nào cho tháng này' : '';
  const match = /(\d{1,2}\/\d{1,2}\/\d{4})\D+(\d{1,2}:\d{2})/.exec(best.text);
  return `Số liệu cập nhật lúc ${match ? `${match[2]}, ${match[1]}` : best.text}`;
}

const LINK_KINDS: Record<string, string> = { video: 'video', photo: 'ảnh', post: 'post' };

export type LinkKind = 'video' | 'photo' | 'post' | '';

/** What a post link points at: a TikTok video or photo post, or a Threads post. */
export function linkKind(url: string): LinkKind {
  const match = /\/@[^/]+\/(video|photo|post)\//.exec(url);
  return match ? (match[1] as LinkKind) : '';
}

/** "https://www.tiktok.com/@kenh/video/7391…123456" -> { handle: "@kenh", detail: "video …123456" }. */
export function shortLink(url: string): { handle: string; detail: string } {
  try {
    const parsed = new URL(url);
    const parts = parsed.pathname.split('/').filter(Boolean);
    const handleIndex = parts.findIndex(part => part.startsWith('@'));
    if (handleIndex >= 0) {
      const kind = LINK_KINDS[parts[handleIndex + 1]] || '';
      const id = parts[handleIndex + 2] || '';
      const shortId = id.length > 10 ? `…${id.slice(-6)}` : id;
      const handle = decodeURIComponent(parts[handleIndex]);
      return { handle: handle.length > 22 ? `${handle.slice(0, 21)}…` : handle, detail: [kind, shortId].filter(Boolean).join(' ') };
    }
    const text = parsed.hostname.replace(/^www\./, '') + parsed.pathname.replace(/\/$/, '');
    return { handle: text.length > 34 ? `${text.slice(0, 33)}…` : text, detail: '' };
  } catch {
    return { handle: url.length > 34 ? `${url.slice(0, 33)}…` : url, detail: '' };
  }
}
