// Sheet-name rules (summary tabs, result tabs, hidden sheets), ported from app.js.
import { normalizeVietnameseKey } from './format';
import { PLATFORMS, PLATFORM_KEYS, platformConfig, type PlatformKey } from './platforms';

export function isSummarySheetName(value: unknown): boolean {
  const key = normalizeVietnameseKey(value);
  return key === 'tong ket' || key.startsWith('tong ket ');
}

export function summarySheetPlatform(value: unknown): PlatformKey | '' {
  if (!isSummarySheetName(value)) return '';
  const key = normalizeVietnameseKey(value);
  const tagged = PLATFORM_KEYS.find(platform => {
    const tag = normalizeVietnameseKey(PLATFORMS[platform].summaryTag);
    return tag && (key === `tong ket ${tag}` || key.startsWith(`tong ket ${tag} `));
  });
  return tagged || PLATFORM_KEYS.find(platform => !PLATFORMS[platform].summaryTag) || '';
}

export function isResultSheetName(value: unknown): boolean {
  const key = normalizeVietnameseKey(value);
  return PLATFORM_KEYS.some(platform => key.startsWith(`report seeding ${normalizeVietnameseKey(PLATFORMS[platform].label)}`))
    || /^(?:T\d{1,2}\s+)?\d{2}-\d{2}-\d{4}-\d{2}[:-]?\d{2}(?:-\d+)?$/.test(String(value || '').trim());
}

/** Each platform sees its data sheets and its own summary tabs, never the other platform's. */
export function visibleSheetTabs(sheets: readonly string[] | undefined, platform: PlatformKey): string[] {
  return (Array.isArray(sheets) ? sheets : []).filter(sheet => {
    if (!sheet) return false;
    if (isSummarySheetName(sheet)) return summarySheetPlatform(sheet) === platform;
    return !(platformConfig(platform).hidesResultSheetTabs && isResultSheetName(sheet));
  });
}

export function filterDataSheets(sheets: readonly unknown[] | undefined): string[] {
  return (Array.isArray(sheets) ? sheets : []).filter(
    (sheet): sheet is string => typeof sheet === 'string' && Boolean(sheet) && !isSummarySheetName(sheet) && !isResultSheetName(sheet),
  );
}

/** Sheets hidden in Excel go after the visible ones; selects mark them "(ẩn)". */
export function dataSheetsVisibleFirst(sheets: readonly string[] | undefined, hiddenSheets: readonly string[] = []): string[] {
  const hidden = new Set(hiddenSheets);
  const dataSheets = filterDataSheets(sheets);
  return [...dataSheets.filter(sheet => !hidden.has(sheet)), ...dataSheets.filter(sheet => hidden.has(sheet))];
}

export function sheetOptionLabel(sheet: string, hiddenSheets: readonly string[] = []): string {
  return hiddenSheets.includes(sheet) ? `${sheet} (ẩn)` : sheet;
}

export function dataSheetNameForSummaryTab(summaryTabName: string, sheets: readonly string[]): string {
  const prefix = 'Tổng kết ';
  const text = String(summaryTabName || '').trim();
  if (!text.toLowerCase().startsWith(prefix.toLowerCase())) return '';
  const suffix = text.slice(prefix.length).trim().toLowerCase();
  if (!suffix) return '';
  return (Array.isArray(sheets) ? sheets : []).find(sheet => String(sheet || '').trim().toLowerCase() === suffix) || '';
}

/** Choose a value for a sheet select the way renderSheetSelect did. */
export function pickSheet(validSheets: readonly string[], preferred: string, current = ''): string {
  if (!validSheets.length) return '';
  if (preferred && validSheets.includes(preferred)) return preferred;
  if (current && validSheets.includes(current)) return current;
  return validSheets[0];
}
