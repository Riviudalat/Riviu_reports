// Partner report helpers, ported from app.js.
import { fileTimestamp } from './format';

export const DEFAULT_MIN_VIEWS = 100;

export interface ReportPartner {
  name: string;
  linkCount: number | null;
  rawLinkCount: number | null;
}

export function normalizeReportPartners(raw: unknown): ReportPartner[] {
  return (Array.isArray(raw) ? raw : [])
    .map((item): ReportPartner => {
      if (typeof item === 'string') return { name: item, linkCount: null, rawLinkCount: null };
      const record = (item || {}) as Record<string, unknown>;
      const linkCount = Number(record.linkCount);
      const rawLinkCount = Number(record.rawLinkCount);
      return {
        name: String(record.name || '').trim(),
        linkCount: Number.isFinite(linkCount) ? linkCount : 0,
        rawLinkCount: Number.isFinite(rawLinkCount) ? rawLinkCount : 0,
      };
    })
    .filter(item => item.name);
}

/**
 * Partners always come with links from their partner columns; linkCount counts only the rows
 * a report would export (metrics read and above the min-views filter). An empty label means
 * the partner has exportable links.
 */
export function partnerLinkStatusText(item: Pick<ReportPartner, 'linkCount' | 'rawLinkCount'>): string {
  const rawLinkCount = Number(item.rawLinkCount) || 0;
  const linkCount = Number(item.linkCount) || 0;
  if (rawLinkCount <= 0) return linkCount > 0 ? '' : 'Chưa có link';
  if (linkCount > 0) return '';
  return `${rawLinkCount} link · chưa có số liệu hoặc dưới ngưỡng view`;
}

/** Every platform's partner report uses the same min-views threshold. */
export function reportFilterParams(applyMinViews: boolean, minViewsInput: string | number) {
  const minViewsRaw = parseInt(String(minViewsInput), 10);
  const minViews = Number.isFinite(minViewsRaw) && minViewsRaw >= 0 ? minViewsRaw : DEFAULT_MIN_VIEWS;
  return { applyMinViews, minViews };
}

export function buildExportFallbackFilename(partners: readonly string[], sheetName: string, now = new Date()): string {
  const stamp = fileTimestamp(now);
  const sheet = String(sheetName || '').trim();
  if (partners.length === 1) {
    const parts = [partners[0]];
    if (sheet) parts.push(sheet);
    parts.push(stamp);
    return `${parts.join(' ')}.xlsx`;
  }
  return sheet ? `bao_cao_doi_tac ${sheet} ${stamp}.zip` : `bao_cao_doi_tac ${stamp}.zip`;
}
