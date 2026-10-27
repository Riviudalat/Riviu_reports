// Number and text formatting. Invariant: blank/null/undefined means "unknown" and renders
// blank; 0 is a confirmed zero and renders "0". Totals add the known values only.

export type MetricValue = number | string | null | undefined;

/** Vietnamese grouping ("1.234.567"); a non-finite input renders "0" like the old UI. */
export function formatNumber(value: unknown): string {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString('vi-VN') : '0';
}

export function isUnknownMetric(value: unknown): boolean {
  return value === null || value === undefined || value === '';
}

/** Unknown metrics stay blank on every platform; only a confirmed number (including 0) is shown. */
export function formatResultNumber(value: unknown): string {
  return isUnknownMetric(value) ? '' : formatNumber(value);
}

/** Parse a metric cell: numbers pass through, "1.234" / "1,234" strings are read as integers. */
export function metricNumber(value: unknown): number | null {
  if (isUnknownMetric(value)) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const text = String(value).trim().replace(/[.,\s]/g, '');
  if (!/^-?\d+$/.test(text)) return null;
  return Number(text);
}

/**
 * Sum the known values of a column. Returns null (rendered blank) only when no value in the
 * column is known; a column of confirmed zeros sums to 0.
 */
export function sumKnown(values: Iterable<unknown>): number | null {
  let total: number | null = null;
  for (const value of values) {
    const number = metricNumber(value);
    if (number === null) continue;
    total = (total ?? 0) + number;
  }
  return total;
}

export function formatDuration(seconds: unknown): string {
  if (seconds === null || seconds === undefined || seconds === '') return '';
  const sec = Math.max(parseInt(String(seconds), 10) || 0, 0);
  if (sec < 60) return `${sec}s`;
  const minutes = Math.floor(sec / 60);
  const remain = sec % 60;
  if (minutes < 60) return `${minutes}m ${remain}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function normalizeVietnameseKey(value: unknown): string {
  return String(value || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

/** dd-mm-yyyy-HH-MM, the local timestamp used in exported file names. */
export function fileTimestamp(now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(now.getDate())}-${pad(now.getMonth() + 1)}-${now.getFullYear()}-${pad(now.getHours())}-${pad(now.getMinutes())}`;
}

export function filenameFromDisposition(disposition: string, fallback: string): string {
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) return decodeURIComponent(utf8Match[1]);
  const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
  return plainMatch ? plainMatch[1] : fallback;
}

export function formatCookieCheckedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** navigator.clipboard is missing outside secure contexts; fall back to a hidden textarea. */
export async function copyText(text: string) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}
